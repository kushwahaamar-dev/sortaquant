"""Paper trading: live market data, virtual fills, no signed orders."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from sortaquant.adapters.binance import BinanceWebSocketAdapter
from sortaquant.core.types import BBO, BookLevel, Trade
from sortaquant.sim.simulator import SimulationSummary, TickSimulator


class SessionPhase(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LIVE = "live"
    HALTED = "halted"
    STOPPED = "stopped"


HeartbeatFn = Callable[["PaperSnapshot"], None]


@dataclass
class PaperSnapshot:
    phase: SessionPhase
    symbol: str
    bbo_count: int
    trade_count: int
    fills: int
    position: float
    net_pnl: float
    markout_1s_avg_bps: float
    halted: bool
    last_mid: float
    elapsed_s: float
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    feed_url: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["phase"] = self.phase.value
        return d


@dataclass
class PaperSession:
    """Owns the live feed and the in-process simulator for one paper run."""

    symbol: str = "BTCFDUSD"
    is_perp: bool = False
    maker_fee_bps: float = 0.0
    data_host: Optional[str] = None
    heartbeat_s: float = 5.0
    on_heartbeat: Optional[HeartbeatFn] = None
    phase: SessionPhase = SessionPhase.IDLE
    bbo_count: int = 0
    trade_count: int = 0
    started_at_s: float = 0.0
    _adapter: BinanceWebSocketAdapter = field(init=False, repr=False)
    _sim: TickSimulator = field(init=False, repr=False)
    _last_heartbeat_s: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        sym = self.symbol.replace("/", "").replace("-", "").upper()
        self.symbol = sym
        self._adapter = BinanceWebSocketAdapter(
            symbol=sym.lower(),
            is_perp=self.is_perp,
            data_host=self.data_host,
        )
        self._sim = TickSimulator(symbol=sym, maker_fee_bps=self.maker_fee_bps)
        self._adapter.on_depth = self._on_depth
        self._adapter.on_trade = self._on_trade

    @property
    def simulator(self) -> TickSimulator:
        return self._sim

    def _maybe_heartbeat(self) -> None:
        if self.on_heartbeat is None or self.heartbeat_s <= 0:
            return
        now = time.time()
        if now - self._last_heartbeat_s < self.heartbeat_s:
            return
        self._last_heartbeat_s = now
        self.on_heartbeat(self.snapshot())

    def _on_depth(self, bbo: BBO, bids: List[BookLevel], asks: List[BookLevel]) -> None:
        self.bbo_count += 1
        self._sim.step_bbo(bbo, bids=bids, asks=asks)
        if self._sim.risk_governor.is_drawdown_halted or self._sim.regime_engine.is_halted:
            self.phase = SessionPhase.HALTED
        elif self.phase == SessionPhase.CONNECTING:
            self.phase = SessionPhase.LIVE
        self._maybe_heartbeat()

    def _on_trade(self, trade: Trade) -> None:
        self.trade_count += 1
        self._sim.step_trade(trade)
        self._maybe_heartbeat()

    def snapshot(self) -> PaperSnapshot:
        summary = self._sim.summarize()
        return PaperSnapshot(
            phase=self.phase,
            symbol=self.symbol,
            bbo_count=self.bbo_count,
            trade_count=self.trade_count,
            fills=summary.total_trades,
            position=self._sim.risk_governor.current_position,
            net_pnl=summary.total_net_pnl,
            markout_1s_avg_bps=summary.markout_1s_avg_bps,
            halted=self.phase == SessionPhase.HALTED or self._sim.last_halted,
            last_mid=self._sim._last_mid,
            elapsed_s=max(0.0, time.time() - self.started_at_s) if self.started_at_s else 0.0,
            bid_price=self._sim.last_bid_price,
            ask_price=self._sim.last_ask_price,
            bid_size=self._sim.last_bid_size,
            ask_size=self._sim.last_ask_size,
            feed_url=self._adapter.ws_endpoint,
        )

    async def run(self, duration_s: float = 30.0) -> SimulationSummary:
        """Connect to Binance and paper-trade for duration_s seconds."""
        self.phase = SessionPhase.CONNECTING
        self.started_at_s = time.time()
        self._last_heartbeat_s = 0.0
        await self._adapter.connect()
        try:
            frames = await self._adapter.run(duration_s=duration_s)
            if frames == 0 and self.bbo_count == 0:
                raise RuntimeError(
                    f"No market-data frames from {self._adapter.ws_endpoint}. "
                    "Check symbol and network."
                )
        finally:
            await self._adapter.disconnect()
            if self.phase != SessionPhase.HALTED:
                self.phase = SessionPhase.STOPPED
        return self._sim.summarize()

    def run_blocking(self, duration_s: float = 30.0) -> SimulationSummary:
        return asyncio.run(self.run(duration_s=duration_s))

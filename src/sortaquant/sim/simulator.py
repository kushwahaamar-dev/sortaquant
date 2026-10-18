"""Tick-Level Discrete-Event Market Making Simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
import numpy as np

from sortaquant.core.types import BBO, BookLevel, Fill, RegimeState, Side, Trade
from sortaquant.core.orderbook import OrderBookL2
from sortaquant.features import MicrostructureFeatureEngine
from sortaquant.regime.engine import RegimeEngine
from sortaquant.quoting.engine import QuotingEngine
from sortaquant.risk.governor import DeterministicRiskGovernor
from sortaquant.sim.queue_model import OrderQueueTracker
from sortaquant.sim.latency_model import LatencyModel

if TYPE_CHECKING:
    from sortaquant.slow_brain.adapter import SlowBrainAdapter


@dataclass
class SimulatedFillRecord:
    fill_id: str
    order_id: str
    side: Side
    price: float
    size: float
    fee: float
    timestamp_ns: int
    mid_at_fill: float
    regime: RegimeState
    markout_100ms_mid: Optional[float] = None
    markout_1s_mid: Optional[float] = None
    markout_5s_mid: Optional[float] = None
    markout_30s_mid: Optional[float] = None
    markout_1s_reported: bool = False

    @property
    def markout_1s_bps(self) -> Optional[float]:
        if self.markout_1s_mid is None:
            return None
        drift = (self.markout_1s_mid - self.price) / self.price
        sign = 1 if self.side == Side.BUY else -1
        return float(sign * drift * 10_000.0)


@dataclass
class SimulationSummary:
    total_trades: int
    total_volume: float
    realized_pnl: float
    unrealized_pnl: float
    total_net_pnl: float
    maker_fees_paid: float
    sharpe_ratio: float
    max_drawdown_pct: float
    markout_100ms_avg_bps: float
    markout_1s_avg_bps: float
    markout_5s_avg_bps: float
    per_regime_pnl: Dict[str, float]
    per_regime_fills: Dict[str, int]


class TickSimulator:
    """Realistic tick-level matching simulator for RAIM market making."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        maker_fee_bps: float = 0.0,
        stress_latency_2x: bool = False,
        slow_brain: Optional["SlowBrainAdapter"] = None,
    ):
        self.symbol = symbol
        self.maker_fee_rate = maker_fee_bps * 1e-4
        self.slow_brain = slow_brain

        self.orderbook = OrderBookL2(symbol)
        self.features_engine = MicrostructureFeatureEngine(symbol)
        self.regime_engine = RegimeEngine()
        self.quoting_engine = QuotingEngine(symbol=symbol)
        self.risk_governor = DeterministicRiskGovernor()

        self.queue_tracker = OrderQueueTracker()
        self.latency_model = LatencyModel(median_latency_ms=5.0, stress_2x=stress_latency_2x)

        self.current_time_ns: int = 0
        self._last_mid: float = 0.0
        self._prev_mid: Optional[float] = None
        self.fills: List[SimulatedFillRecord] = []
        self.pnl_history: List[Tuple[int, float]] = []

        self._active_bid_id: Optional[str] = None
        self._active_ask_id: Optional[str] = None
        self._order_counter: int = 0
        self.last_bid_price: Optional[float] = None
        self.last_ask_price: Optional[float] = None
        self.last_bid_size: float = 0.0
        self.last_ask_size: float = 0.0
        self.last_halted: bool = False

    def step_bbo(self, bbo: BBO, bids: Optional[List[BookLevel]] = None, asks: Optional[List[BookLevel]] = None) -> None:
        """Processes an L2 book update tick."""
        self.current_time_ns = bbo.timestamp_ns
        self.risk_governor.on_feed_packet(bbo.timestamp_ns)

        if bids and asks:
            self.orderbook.set_snapshot(
                [(b.price, b.size) for b in bids],
                [(a.price, a.size) for a in asks],
                timestamp_ns=bbo.timestamp_ns,
            )

        feats = self.features_engine.on_bbo(bbo, bids=bids, asks=asks)

        if self._prev_mid is not None and self._prev_mid > 0:
            ret_val = (bbo.mid_price - self._prev_mid) / self._prev_mid
        else:
            ret_val = 0.0

        sb = None
        if self.slow_brain is not None:
            sb = self.slow_brain.get_current_state(now_ns=bbo.timestamp_ns)

        regime_out = self.regime_engine.update(
            features_vec=feats.to_vector(),
            return_for_bocpd=ret_val,
            z_vix=sb.z_VIX if sb is not None else 0.0,
            event_risk=sb.EventRisk if sb is not None else 0,
        )

        bid_depth, ask_depth = self.orderbook.get_depth_volume(levels=5)
        tot_depth = bid_depth + ask_depth

        raw_quotes = self.quoting_engine.generate_quotes(
            bbo=bbo,
            inventory=self.risk_governor.current_position,
            volatility=max(0.0001, feats.bipower_vol),
            regime_output=regime_out,
            ofi_value=feats.ofi,
        )

        llm_m = sb.risk_multiplier if sb is not None else 1.0
        governed = self.risk_governor.evaluate_quotes(
            quotes=raw_quotes,
            mid_price=bbo.mid_price,
            volatility=feats.bipower_vol,
            l2_depth=max(0.1, tot_depth),
            now_ns=bbo.timestamp_ns,
            llm_risk_multiplier=llm_m,
        )

        self._update_quotes_in_queue(governed, bbo)
        self.last_bid_price = governed.bid_price
        self.last_ask_price = governed.ask_price
        self.last_bid_size = governed.bid_size
        self.last_ask_size = governed.ask_size
        self.last_halted = bool(governed.is_halted or self.regime_engine.is_halted)

        total_pnl = self.risk_governor.equity(bbo.mid_price) - self.risk_governor.config.capital_budget
        self.pnl_history.append((bbo.timestamp_ns, total_pnl))

        self._update_markouts(bbo.mid_price, bbo.timestamp_ns)
        self._prev_mid = bbo.mid_price
        self._last_mid = bbo.mid_price

    def run_tape(self, bbos: List[BBO], trades: List[Trade]) -> None:
        """Causal event loop: trades strictly before each BBO, then the BBO, then flush."""
        trade_idx = 0
        n_trades = len(trades)
        for bbo in bbos:
            while trade_idx < n_trades and trades[trade_idx].timestamp_ns < bbo.timestamp_ns:
                self.step_trade(trades[trade_idx])
                trade_idx += 1
            self.step_bbo(bbo)
        while trade_idx < n_trades:
            self.step_trade(trades[trade_idx])
            trade_idx += 1

    def step_trade(self, trade: Trade) -> None:
        """Processes an external trade tape tick and checks for quote fills."""
        self.current_time_ns = trade.timestamp_ns
        self.features_engine.on_trade(trade)

        mid = self._last_mid if self._last_mid > 0 else trade.price

        matched_fills = self.queue_tracker.on_market_trade(
            trade_price=trade.price,
            trade_size=trade.size,
            trade_side=trade.side,
            trade_time_ns=trade.timestamp_ns,
        )

        for order_id, fill_size in matched_fills:
            side = Side.BUY if order_id == self._active_bid_id else Side.SELL
            fee = fill_size * trade.price * self.maker_fee_rate

            fill_record = Fill(
                fill_id=f"f_{len(self.fills)+1}",
                order_id=order_id,
                symbol=self.symbol,
                side=side,
                price=trade.price,
                size=fill_size,
                fee=fee,
                is_maker=True,
                timestamp_ns=trade.timestamp_ns,
                mid_at_fill=mid,
            )
            self.risk_governor.on_fill(fill_record, current_mid=mid)

            self.fills.append(
                SimulatedFillRecord(
                    fill_id=fill_record.fill_id,
                    order_id=order_id,
                    side=side,
                    price=trade.price,
                    size=fill_size,
                    fee=fee,
                    timestamp_ns=trade.timestamp_ns,
                    mid_at_fill=mid,
                    regime=(
                        self.regime_engine._last_output.active_regime
                        if self.regime_engine._last_output
                        else RegimeState.R1_CALM
                    ),
                )
            )

    def _update_quotes_in_queue(self, governed, bbo: BBO) -> None:
        """Places or replaces quotes with sampled ack delay before they are matchable.

        Discrete tick rounding keeps queue priority across small mid noise so FIFO
        ahead-volume can actually be consumed between requotes.
        """
        tick = 0.01
        ack_ns = self.latency_model.sample_latency_ns()

        def _round_px(px: float) -> float:
            return round(px / tick) * tick

        if governed.bid_price is None or governed.bid_size <= 0:
            if self._active_bid_id:
                self.queue_tracker.cancel_order(self._active_bid_id)
                self._active_bid_id = None
        else:
            bid_px = _round_px(governed.bid_price)
            existing = self.queue_tracker.get_order(self._active_bid_id) if self._active_bid_id else None
            if existing is not None and abs(existing.price - bid_px) <= tick / 2:
                existing.remaining_size = governed.bid_size
                existing.size = governed.bid_size
            else:
                if self._active_bid_id:
                    self.queue_tracker.cancel_order(self._active_bid_id)
                self._order_counter += 1
                new_id = f"bid_{self._order_counter}"
                self._active_bid_id = new_id
                touch = _round_px(bbo.bid_price)
                if bid_px > touch + tick / 2:
                    depth_ahead = 0.0
                elif abs(bid_px - touch) <= tick / 2:
                    depth_ahead = bbo.bid_size
                else:
                    depth_ahead = max(bbo.bid_size, 5.0)
                self.queue_tracker.place_order(
                    order_id=new_id,
                    side=Side.BUY,
                    price=bid_px,
                    size=governed.bid_size,
                    existing_level_depth=depth_ahead,
                    timestamp_ns=bbo.timestamp_ns,
                    ack_delay_ns=ack_ns,
                )

        if governed.ask_price is None or governed.ask_size <= 0:
            if self._active_ask_id:
                self.queue_tracker.cancel_order(self._active_ask_id)
                self._active_ask_id = None
        else:
            ask_px = _round_px(governed.ask_price)
            existing = self.queue_tracker.get_order(self._active_ask_id) if self._active_ask_id else None
            if existing is not None and abs(existing.price - ask_px) <= tick / 2:
                existing.remaining_size = governed.ask_size
                existing.size = governed.ask_size
            else:
                if self._active_ask_id:
                    self.queue_tracker.cancel_order(self._active_ask_id)
                self._order_counter += 1
                new_id = f"ask_{self._order_counter}"
                self._active_ask_id = new_id
                touch = _round_px(bbo.ask_price)
                if ask_px < touch - tick / 2:
                    depth_ahead = 0.0
                elif abs(ask_px - touch) <= tick / 2:
                    depth_ahead = bbo.ask_size
                else:
                    depth_ahead = max(bbo.ask_size, 5.0)
                self.queue_tracker.place_order(
                    order_id=new_id,
                    side=Side.SELL,
                    price=ask_px,
                    size=governed.ask_size,
                    existing_level_depth=depth_ahead,
                    timestamp_ns=bbo.timestamp_ns,
                    ack_delay_ns=ack_ns,
                )

    def _update_markouts(self, current_mid: float, now_ns: int) -> None:
        """Evaluates forward mid-price markouts and feeds 1s adverse results to the governor."""
        for f in self.fills:
            dt = now_ns - f.timestamp_ns
            if f.markout_100ms_mid is None and dt >= 100_000_000:
                f.markout_100ms_mid = current_mid
            if f.markout_1s_mid is None and dt >= 1_000_000_000:
                f.markout_1s_mid = current_mid
            if f.markout_5s_mid is None and dt >= 5_000_000_000:
                f.markout_5s_mid = current_mid
            if f.markout_30s_mid is None and dt >= 30_000_000_000:
                f.markout_30s_mid = current_mid

            if f.markout_1s_mid is not None and not f.markout_1s_reported:
                f.markout_1s_reported = True
                bps = f.markout_1s_bps
                if bps is not None and bps < 0:
                    self.risk_governor.on_adverse_markout(now_ns)
                elif bps is not None and bps >= 0:
                    self.risk_governor.on_favorable_markout()

    def summarize(self) -> SimulationSummary:
        """Computes performance, risk, and markout metrics from the cash ledger."""
        total_trades = len(self.fills)
        total_vol = sum(f.size for f in self.fills)
        fees_paid = sum(f.fee for f in self.fills)

        last_mid = self._last_mid if self._last_mid > 0 else 0.0
        realized = self.risk_governor.realized_pnl
        unrealized = self.risk_governor.unrealized_pnl(last_mid) if last_mid > 0 else 0.0
        total_net = (
            self.risk_governor.equity(last_mid) - self.risk_governor.config.capital_budget
            if last_mid > 0
            else realized
        )

        mo_100 = [
            (f.markout_100ms_mid - f.price) / f.price * (1 if f.side == Side.BUY else -1) * 10_000
            for f in self.fills
            if f.markout_100ms_mid is not None
        ]
        mo_1s = [
            (f.markout_1s_mid - f.price) / f.price * (1 if f.side == Side.BUY else -1) * 10_000
            for f in self.fills
            if f.markout_1s_mid is not None
        ]
        mo_5s = [
            (f.markout_5s_mid - f.price) / f.price * (1 if f.side == Side.BUY else -1) * 10_000
            for f in self.fills
            if f.markout_5s_mid is not None
        ]

        avg_mo_100 = float(np.mean(mo_100)) if mo_100 else 0.0
        avg_mo_1s = float(np.mean(mo_1s)) if mo_1s else 0.0
        avg_mo_5s = float(np.mean(mo_5s)) if mo_5s else 0.0

        pnls = [p for _, p in self.pnl_history] if self.pnl_history else [0.0]
        hwm = np.maximum.accumulate(pnls)
        dds = hwm - pnls
        max_dd = float(np.max(dds)) if len(dds) > 0 else 0.0
        max_dd_pct = max_dd / self.risk_governor.config.capital_budget

        pnl_diffs = np.diff(pnls) if len(pnls) > 1 else np.array([0.0])
        std = np.std(pnl_diffs)
        sharpe = float(np.mean(pnl_diffs) / std * np.sqrt(252 * 86400 / 60)) if std > 1e-9 else 0.0

        per_regime_pnl: Dict[str, float] = {}
        per_regime_fills: Dict[str, int] = {}
        for r in RegimeState:
            reg_fills = [f for f in self.fills if f.regime == r]
            per_regime_fills[r.name] = len(reg_fills)
            per_regime_pnl[r.name] = sum(
                (f.markout_1s_mid - f.price) * f.size * (1 if f.side == Side.BUY else -1)
                for f in reg_fills
                if f.markout_1s_mid is not None
            )

        return SimulationSummary(
            total_trades=total_trades,
            total_volume=total_vol,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_net_pnl=total_net,
            maker_fees_paid=fees_paid,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd_pct,
            markout_100ms_avg_bps=avg_mo_100,
            markout_1s_avg_bps=avg_mo_1s,
            markout_5s_avg_bps=avg_mo_5s,
            per_regime_pnl=per_regime_pnl,
            per_regime_fills=per_regime_fills,
        )

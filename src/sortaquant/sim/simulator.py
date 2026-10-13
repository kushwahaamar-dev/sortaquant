"""Tick-Level Discrete-Event Market Making Simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

from sortaquant.core.types import BBO, BookLevel, Fill, Order, OrderStatus, RegimeState, Side, Trade
from sortaquant.core.orderbook import OrderBookL2
from sortaquant.features import MicrostructureFeatureEngine
from sortaquant.regime.engine import RegimeEngine
from sortaquant.quoting.engine import QuotingEngine
from sortaquant.risk.governor import DeterministicRiskGovernor
from sortaquant.sim.queue_model import OrderQueueTracker
from sortaquant.sim.latency_model import LatencyModel


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

    @property
    def markout_1s_bps(self) -> Optional[float]:
        if self.markout_1s_mid is None:
            return None
        drift = (self.markout_1s_mid - self.price) / self.price
        sign = 1 if self.side == Side.BUY else -1
        # Directional markout: positive means price moved in our favor (profitable)
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
        maker_fee_bps: float = 0.0,  # Zero maker fee promo (or -0.5 bps rebate)
        stress_latency_2x: bool = False,
    ):
        self.symbol = symbol
        self.maker_fee_rate = maker_fee_bps * 1e-4

        # Core Engines
        self.orderbook = OrderBookL2(symbol)
        self.features_engine = MicrostructureFeatureEngine(symbol)
        self.regime_engine = RegimeEngine()
        self.quoting_engine = QuotingEngine(symbol=symbol)
        self.risk_governor = DeterministicRiskGovernor()

        self.queue_tracker = OrderQueueTracker()
        self.latency_model = LatencyModel(median_latency_ms=5.0, stress_2x=stress_latency_2x)

        # Simulator State
        self.current_time_ns: int = 0
        self.fills: List[SimulatedFillRecord] = []
        self.pnl_history: List[Tuple[int, float]] = []

        # Current active quotes
        self._active_bid_id: Optional[str] = None
        self._active_ask_id: Optional[str] = None
        self._order_counter: int = 0

    def step_bbo(self, bbo: BBO, bids: Optional[List[BookLevel]] = None, asks: Optional[List[BookLevel]] = None) -> None:
        """Processes an L2 book update tick."""
        self.current_time_ns = bbo.timestamp_ns
        self.risk_governor.on_feed_packet(bbo.timestamp_ns)

        # 1. Update orderbook
        if bids and asks:
            self.orderbook.set_snapshot(
                [(b.price, b.size) for b in bids],
                [(a.price, a.size) for a in asks],
                timestamp_ns=bbo.timestamp_ns,
            )

        # 2. Extract features
        feats = self.features_engine.on_bbo(bbo, bids=bids, asks=asks)

        # 3. Update regime
        ret_val = (bbo.mid_price - feats.mid_price) / feats.mid_price if feats.mid_price > 0 else 0.0
        regime_out = self.regime_engine.update(
            features_vec=feats.to_vector(),
            return_for_bocpd=ret_val,
        )

        # 4. Generate optimal quotes
        bid_depth, ask_depth = self.orderbook.get_depth_volume(levels=5)
        tot_depth = bid_depth + ask_depth

        raw_quotes = self.quoting_engine.generate_quotes(
            bbo=bbo,
            inventory=self.risk_governor.current_position,
            volatility=max(0.0001, feats.bipower_vol),
            regime_output=regime_out,
            ofi_value=feats.ofi,
        )

        # 5. Apply deterministic risk governor
        governed = self.risk_governor.evaluate_quotes(
            quotes=raw_quotes,
            mid_price=bbo.mid_price,
            volatility=feats.bipower_vol,
            l2_depth=tot_depth,
            now_ns=bbo.timestamp_ns,
        )

        # 6. Update order book queue
        self._update_quotes_in_queue(governed, bbo)

        # 7. Record equity curve
        unrealized = self.risk_governor.current_position * bbo.mid_price
        total_pnl = self.risk_governor.realized_pnl + unrealized
        self.pnl_history.append((bbo.timestamp_ns, total_pnl))

        # 8. Check pending markout milestones
        self._update_markouts(bbo.mid_price, bbo.timestamp_ns)

    def step_trade(self, trade: Trade) -> None:
        """Processes an external trade tape tick and checks for quote fills."""
        self.current_time_ns = trade.timestamp_ns
        self.features_engine.on_trade(trade)

        # Match trade against our resting queue orders
        matched_fills = self.queue_tracker.on_market_trade(
            trade_price=trade.price,
            trade_size=trade.size,
            trade_side=trade.side,
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
                mid_at_fill=trade.price,
            )
            # Update governor
            self.risk_governor.on_fill(fill_record, current_mid=trade.price)

            # Store record for markout analysis
            self.fills.append(
                SimulatedFillRecord(
                    fill_id=fill_record.fill_id,
                    order_id=order_id,
                    side=side,
                    price=trade.price,
                    size=fill_size,
                    fee=fee,
                    timestamp_ns=trade.timestamp_ns,
                    mid_at_fill=trade.price,
                    regime=self.regime_engine._last_output.active_regime if self.regime_engine._last_output else RegimeState.R1_CALM,
                )
            )

    def _update_quotes_in_queue(self, governed, bbo: BBO) -> None:
        """Places or replaces quotes in the FIFO queue tracker with simulated ack delays."""
        # Bid side update
        if governed.bid_price is None or governed.bid_size <= 0:
            if self._active_bid_id:
                self.queue_tracker.cancel_order(self._active_bid_id)
                self._active_bid_id = None
        else:
            existing = self.queue_tracker.get_order(self._active_bid_id) if self._active_bid_id else None
            # If price changed, cancel and replace at back of queue
            if existing is None or abs(existing.price - governed.bid_price) > 1e-6:
                if self._active_bid_id:
                    self.queue_tracker.cancel_order(self._active_bid_id)
                self._order_counter += 1
                new_id = f"bid_{self._order_counter}"
                self._active_bid_id = new_id
                # Ahead volume is existing book volume at that level
                depth_ahead = bbo.bid_size if governed.bid_price == bbo.bid_price else 5.0
                self.queue_tracker.place_order(
                    order_id=new_id,
                    side=Side.BUY,
                    price=governed.bid_price,
                    size=governed.bid_size,
                    existing_level_depth=depth_ahead,
                    timestamp_ns=bbo.timestamp_ns,
                )

        # Ask side update
        if governed.ask_price is None or governed.ask_size <= 0:
            if self._active_ask_id:
                self.queue_tracker.cancel_order(self._active_ask_id)
                self._active_ask_id = None
        else:
            existing = self.queue_tracker.get_order(self._active_ask_id) if self._active_ask_id else None
            if existing is None or abs(existing.price - governed.ask_price) > 1e-6:
                if self._active_ask_id:
                    self.queue_tracker.cancel_order(self._active_ask_id)
                self._order_counter += 1
                new_id = f"ask_{self._order_counter}"
                self._active_ask_id = new_id
                depth_ahead = bbo.ask_size if governed.ask_price == bbo.ask_price else 5.0
                self.queue_tracker.place_order(
                    order_id=new_id,
                    side=Side.SELL,
                    price=governed.ask_price,
                    size=governed.ask_size,
                    existing_level_depth=depth_ahead,
                    timestamp_ns=bbo.timestamp_ns,
                )

    def _update_markouts(self, current_mid: float, now_ns: int) -> None:
        """Evaluates forward mid-price markouts for past fills."""
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

    def summarize(self) -> SimulationSummary:
        """Computes comprehensive performance, risk, and markout metrics."""
        total_trades = len(self.fills)
        total_vol = sum(f.size for f in self.fills)
        fees_paid = sum(f.fee for f in self.fills)

        last_pnl = self.pnl_history[-1][1] if self.pnl_history else 0.0

        # Markout averages
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

        # Max drawdown
        pnls = [p for _, p in self.pnl_history] if self.pnl_history else [0.0]
        hwm = np.maximum.accumulate(pnls)
        dds = hwm - pnls
        max_dd = float(np.max(dds)) if len(dds) > 0 else 0.0
        max_dd_pct = max_dd / self.risk_governor.config.capital_budget

        # Sharpe ratio
        pnl_diffs = np.diff(pnls) if len(pnls) > 1 else np.array([0.0])
        std = np.std(pnl_diffs)
        sharpe = float(np.mean(pnl_diffs) / std * np.sqrt(252 * 86400 / 60)) if std > 1e-9 else 0.0

        # Per regime attribution
        per_regime_pnl: Dict[str, float] = {}
        per_regime_fills: Dict[str, int] = {}
        for r in RegimeState:
            reg_fills = [f for f in self.fills if f.regime == r]
            per_regime_fills[r.name] = len(reg_fills)
            # Rough attribution
            per_regime_pnl[r.name] = sum(
                (f.markout_1s_mid - f.price) * f.size * (1 if f.side == Side.BUY else -1)
                for f in reg_fills
                if f.markout_1s_mid is not None
            )

        return SimulationSummary(
            total_trades=total_trades,
            total_volume=total_vol,
            realized_pnl=self.risk_governor.realized_pnl,
            unrealized_pnl=last_pnl - self.risk_governor.realized_pnl,
            total_net_pnl=last_pnl - fees_paid,
            maker_fees_paid=fees_paid,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd_pct,
            markout_100ms_avg_bps=avg_mo_100,
            markout_1s_avg_bps=avg_mo_1s,
            markout_5s_avg_bps=avg_mo_5s,
            per_regime_pnl=per_regime_pnl,
            per_regime_fills=per_regime_fills,
        )

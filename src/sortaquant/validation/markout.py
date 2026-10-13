"""Post-Trade Price Markout and Adverse Selection Attribution Engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np

from sortaquant.core.types import RegimeState, Side
from sortaquant.sim.simulator import SimulatedFillRecord


@dataclass
class MarkoutReport:
    total_fills: int
    buy_fills: int
    sell_fills: int
    as_100ms_bps: float
    as_1s_bps: float
    as_5s_bps: float
    as_30s_bps: float
    is_gate1_healthy: bool
    per_regime_as: Dict[str, float]


class MarkoutAnalyzer:
    """Computes rigorous post-trade markouts to quantify adverse selection."""

    def __init__(self, target_half_spread_bps: float = 0.60):
        self.target_half_spread_bps = target_half_spread_bps

    def generate_report(self, fills: List[SimulatedFillRecord]) -> MarkoutReport:
        if not fills:
            return MarkoutReport(
                total_fills=0,
                buy_fills=0,
                sell_fills=0,
                as_100ms_bps=0.0,
                as_1s_bps=0.0,
                as_5s_bps=0.0,
                as_30s_bps=0.0,
                is_gate1_healthy=True,
                per_regime_as={},
            )

        buy_count = sum(1 for f in fills if f.side == Side.BUY)
        sell_count = sum(1 for f in fills if f.side == Side.SELL)

        # Adverse selection calculation:
        # A fill on bid (BUY) suffers adverse selection if price drops after fill: (P_fill - P_future)
        # In bps:
        def calc_as(horizon_getter):
            vals = []
            for f in fills:
                fut_price = horizon_getter(f)
                if fut_price is not None:
                    # Directional drift relative to fill:
                    # If we BOUGHT at 100 and price dropped to 99.9, loss is 0.1 (toxic)
                    # If we SOLD at 100 and price rose to 100.1, loss is 0.1 (toxic)
                    if f.side == Side.BUY:
                        drift = (f.price - fut_price) / f.price
                    else:
                        drift = (fut_price - f.price) / f.price
                    vals.append(drift * 10_000.0)
            return float(np.mean(vals)) if vals else 0.0

        as_100ms = calc_as(lambda f: f.markout_100ms_mid)
        as_1s = calc_as(lambda f: f.markout_1s_mid)
        as_5s = calc_as(lambda f: f.markout_5s_mid)
        as_30s = calc_as(lambda f: f.markout_30s_mid)

        # Gate 1 check: AS_1s < 0.5 * spread
        is_healthy = as_1s < self.target_half_spread_bps

        per_regime: Dict[str, float] = {}
        for r in RegimeState:
            r_fills = [f for f in fills if f.regime == r]
            if r_fills:
                vals = []
                for f in r_fills:
                    fut = f.markout_1s_mid
                    if fut is not None:
                        drift = (f.price - fut) / f.price if f.side == Side.BUY else (fut - f.price) / f.price
                        vals.append(drift * 10_000.0)
                per_regime[r.name] = float(np.mean(vals)) if vals else 0.0

        return MarkoutReport(
            total_fills=len(fills),
            buy_fills=buy_count,
            sell_fills=sell_count,
            as_100ms_bps=as_100ms,
            as_1s_bps=as_1s,
            as_5s_bps=as_5s,
            as_30s_bps=as_30s,
            is_gate1_healthy=is_healthy,
            per_regime_as=per_regime,
        )

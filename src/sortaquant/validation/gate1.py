"""Gate 1 Economic Feasibility Validator and Real-time Circuit Breaker.

Hurdle: maker_fee + E[adverse_selection] < 0.5 * typical_spread.
If violated, trading must halt or instrument must be rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np


@dataclass(frozen=True)
class Gate1Result:
    passed: bool
    maker_fee_bps: float
    expected_as_bps: float
    typical_spread_bps: float
    total_hurdle_cost_bps: float
    half_spread_bps: float
    net_edge_bps: float
    instrument: str
    venue: str
    message: str


class Gate1HurdleValidator:
    """Evaluates economic viability of market making on a given venue and instrument."""

    @staticmethod
    def evaluate(
        maker_fee_bps: float,
        expected_as_bps: float,
        typical_spread_bps: float,
        instrument: str = "BTC/USDT",
        venue: str = "Binance",
    ) -> Gate1Result:
        """Evaluates whether maker_fee + E[AS] < 0.5 * typical_spread.

        All units in basis points (bps).
        """
        half_spread_bps = 0.5 * typical_spread_bps
        total_hurdle_cost_bps = maker_fee_bps + expected_as_bps
        net_edge_bps = half_spread_bps - total_hurdle_cost_bps
        passed = total_hurdle_cost_bps < half_spread_bps

        if passed:
            msg = (
                f"Gate 1 PASSED for {venue}:{instrument}. "
                f"Net edge = {net_edge_bps:+.3f} bps "
                f"(Cost: {total_hurdle_cost_bps:.3f} bps < Half-Spread: {half_spread_bps:.3f} bps)."
            )
        else:
            deficit = total_hurdle_cost_bps - half_spread_bps
            msg = (
                f"Gate 1 FAILED for {venue}:{instrument}! Deficit: {deficit:.3f} bps. "
                f"Hurdle cost ({total_hurdle_cost_bps:.3f} bps) exceeds half-spread ({half_spread_bps:.3f} bps). "
                f"Trading must be halted or instrument replaced."
            )

        return Gate1Result(
            passed=passed,
            maker_fee_bps=maker_fee_bps,
            expected_as_bps=expected_as_bps,
            typical_spread_bps=typical_spread_bps,
            total_hurdle_cost_bps=total_hurdle_cost_bps,
            half_spread_bps=half_spread_bps,
            net_edge_bps=net_edge_bps,
            instrument=instrument,
            venue=venue,
            message=msg,
        )

    @classmethod
    def audit_venues(cls) -> Dict[str, Gate1Result]:
        """Runs the benchmark audit across candidate crypto venues and instruments."""
        presets = {
            "binance_spot_btcusdt_vip0": {
                "venue": "Binance Spot",
                "instrument": "BTC/USDT (VIP 0)",
                "maker_fee_bps": 7.50,
                "expected_as_bps": 0.005,
                "typical_spread_bps": 0.0015,
            },
            "binance_spot_btcfdusd_zerofee": {
                "venue": "Binance Spot",
                "instrument": "BTC/FDUSD (Zero Maker Fee)",
                "maker_fee_bps": 0.00,
                "expected_as_bps": 0.25,
                "typical_spread_bps": 1.20,
            },
            "binance_perp_btcusdt_vip0": {
                "venue": "Binance Perp",
                "instrument": "BTCUSDT Perp (VIP 0)",
                "maker_fee_bps": 2.00,
                "expected_as_bps": 0.015,
                "typical_spread_bps": 0.025,
            },
            "binance_perp_btcusdt_mm_rebate": {
                "venue": "Binance Perp",
                "instrument": "BTCUSDT Perp (MM Tier Rebate)",
                "maker_fee_bps": -0.50,
                "expected_as_bps": 0.015,
                "typical_spread_bps": 0.025,
            },
            "binance_perp_solusdt_vip1": {
                "venue": "Binance Perp",
                "instrument": "SOLUSDT Perp (VIP 1)",
                "maker_fee_bps": 1.20,
                "expected_as_bps": 0.80,
                "typical_spread_bps": 4.50,
            },
        }

        results = {}
        for key, p in presets.items():
            results[key] = cls.evaluate(
                maker_fee_bps=p["maker_fee_bps"],
                expected_as_bps=p["expected_as_bps"],
                typical_spread_bps=p["typical_spread_bps"],
                instrument=p["instrument"],
                venue=p["venue"],
            )
        return results


class RollingGate1Monitor:
    """Real-time streaming monitor for live/paper Gate 1 enforcement."""

    def __init__(
        self,
        maker_fee_bps: float,
        window_size: int = 500,
        instrument: str = "BTC/FDUSD",
        venue: str = "Binance",
    ):
        self.maker_fee_bps = maker_fee_bps
        self.window_size = window_size
        self.instrument = instrument
        self.venue = venue
        self._spread_history: List[float] = []
        self._as_history: List[float] = []

    def record_observation(self, spread_bps: float, realized_as_bps: Optional[float] = None) -> None:
        if spread_bps > 0:
            self._spread_history.append(spread_bps)
            if len(self._spread_history) > self.window_size:
                self._spread_history.pop(0)

        if realized_as_bps is not None:
            self._as_history.append(realized_as_bps)
            if len(self._as_history) > self.window_size:
                self._as_history.pop(0)

    def check_health(self) -> Gate1Result:
        if not self._spread_history:
            return Gate1HurdleValidator.evaluate(
                maker_fee_bps=self.maker_fee_bps,
                expected_as_bps=0.0,
                typical_spread_bps=1.0,
                instrument=self.instrument,
                venue=self.venue,
            )

        typical_spread = float(np.median(self._spread_history))
        expected_as = float(np.mean(self._as_history)) if self._as_history else 0.0

        return Gate1HurdleValidator.evaluate(
            maker_fee_bps=self.maker_fee_bps,
            expected_as_bps=expected_as,
            typical_spread_bps=typical_spread,
            instrument=self.instrument,
            venue=self.venue,
        )

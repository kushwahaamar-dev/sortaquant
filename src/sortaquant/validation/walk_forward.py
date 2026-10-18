"""Purged and Embargoed Walk-Forward Cross-Validation (Lopez de Prado framework)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np

from sortaquant.core.types import BBO, Trade
from sortaquant.features import MicrostructureFeatureEngine
from sortaquant.sim.simulator import TickSimulator


@dataclass
class WalkForwardSplitResult:
    split_index: int
    train_size: int
    test_size: int
    test_sharpe: float
    test_net_pnl: float
    test_max_dd_pct: float
    test_markout_1s_bps: float
    gate1_passed: bool
    embargo_gap: int
    fitted: bool


@dataclass
class WalkForwardReport:
    n_splits: int
    mean_test_sharpe: float
    mean_test_net_pnl: float
    max_test_drawdown_pct: float
    gate1_pass_rate: float
    splits: List[WalkForwardSplitResult]


def _train_feature_matrix(bbos: List[BBO], symbol: str = "BTCFDUSD") -> List[np.ndarray]:
    engine = MicrostructureFeatureEngine(symbol)
    rows: List[np.ndarray] = []
    for bbo in bbos:
        feats = engine.on_bbo(bbo)
        rows.append(feats.to_vector())
    return rows


class WalkForwardValidator:
    """Evaluates RAIM market making out-of-sample using purged and embargoed splits."""

    def __init__(
        self,
        n_splits: int = 4,
        purge_window_ticks: int = 50,
        embargo_window_ticks: int = 50,
        maker_fee_bps: float = 0.0,
    ):
        self.n_splits = n_splits
        self.purge_window_ticks = purge_window_ticks
        self.embargo_window_ticks = embargo_window_ticks
        self.maker_fee_bps = maker_fee_bps

    def run_walk_forward(
        self,
        bbo_ticks: List[BBO],
        trade_ticks: List[Trade],
    ) -> WalkForwardReport:
        """Executes walk-forward evaluation across sequential out-of-sample slices."""
        total_ticks = len(bbo_ticks)
        if total_ticks < 200:
            raise ValueError(f"Need at least 200 ticks for walk-forward, got {total_ticks}")

        split_results: List[WalkForwardSplitResult] = []
        slice_size = total_ticks // (self.n_splits + 1)

        for k in range(self.n_splits):
            train_end = (k + 1) * slice_size
            embargo_gap = self.purge_window_ticks + self.embargo_window_ticks
            test_start = train_end + embargo_gap
            test_end = min(total_ticks, test_start + slice_size)

            if test_start >= test_end:
                break

            train_bbos = bbo_ticks[:train_end]
            test_bbos = bbo_ticks[test_start:test_end]
            test_trades = [
                t
                for t in trade_ticks
                if test_bbos[0].timestamp_ns <= t.timestamp_ns <= test_bbos[-1].timestamp_ns
            ]

            sim = TickSimulator(maker_fee_bps=self.maker_fee_bps)
            fitted = False
            observations = _train_feature_matrix(train_bbos, symbol=sim.symbol)
            if len(observations) >= 50:
                sim.regime_engine.hmm.fit_offline(observations)
                fitted = True

            sim.run_tape(test_bbos, test_trades)
            summary = sim.summarize()
            adverse_1s = -summary.markout_1s_avg_bps
            gate1_ok = (self.maker_fee_bps + adverse_1s) < 0.60

            split_results.append(
                WalkForwardSplitResult(
                    split_index=k,
                    train_size=len(train_bbos),
                    test_size=len(test_bbos),
                    test_sharpe=summary.sharpe_ratio,
                    test_net_pnl=summary.total_net_pnl,
                    test_max_dd_pct=summary.max_drawdown_pct,
                    test_markout_1s_bps=summary.markout_1s_avg_bps,
                    gate1_passed=gate1_ok,
                    embargo_gap=embargo_gap,
                    fitted=fitted,
                )
            )

        mean_sharpe = float(np.mean([s.test_sharpe for s in split_results])) if split_results else 0.0
        mean_pnl = float(np.mean([s.test_net_pnl for s in split_results])) if split_results else 0.0
        max_dd = float(np.max([s.test_max_dd_pct for s in split_results])) if split_results else 0.0
        pass_rate = float(np.mean([1.0 if s.gate1_passed else 0.0 for s in split_results])) if split_results else 0.0

        return WalkForwardReport(
            n_splits=len(split_results),
            mean_test_sharpe=mean_sharpe,
            mean_test_net_pnl=mean_pnl,
            max_test_drawdown_pct=max_dd,
            gate1_pass_rate=pass_rate,
            splits=split_results,
        )

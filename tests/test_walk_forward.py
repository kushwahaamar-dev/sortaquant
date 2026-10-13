"""Unit tests for Purged and Embargoed Walk-Forward Validator and Markout Analyzer."""

import pytest

from sortaquant.adapters.replay import MarketReplayAdapter
from sortaquant.validation.markout import MarkoutAnalyzer
from sortaquant.validation.walk_forward import WalkForwardValidator


def test_markout_analyzer():
    analyzer = MarkoutAnalyzer(target_half_spread_bps=0.60)
    # Empty fills test
    rep_empty = analyzer.generate_report([])
    assert rep_empty.total_fills == 0
    assert rep_empty.is_gate1_healthy is True


def test_walk_forward_cross_validation_splits():
    # Generate 350 ticks of regime dataset
    bbos, trades = MarketReplayAdapter.generate_synthetic_regime_dataset(n_ticks=350, seed=42)

    validator = WalkForwardValidator(
        n_splits=3,
        purge_window_ticks=20,
        embargo_window_ticks=20,
        maker_fee_bps=0.0,
    )

    report = validator.run_walk_forward(bbos, trades)

    assert report.n_splits > 0
    assert len(report.splits) == report.n_splits
    for s in report.splits:
        assert s.train_size > 0
        assert s.test_size > 0
        # Out of sample drawdown must be non-negative
        assert s.test_max_dd_pct >= 0.0

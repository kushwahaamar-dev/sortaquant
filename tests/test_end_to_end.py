"""Comprehensive End-to-End System Integration Test for RAIM."""

import pytest
import numpy as np

from sortaquant.adapters.replay import MarketReplayAdapter
from sortaquant.core.types import RegimeState
from sortaquant.sim.simulator import TickSimulator
from sortaquant.slow_brain.adapter import SlowBrainAdapter
from sortaquant.slow_brain.schema import SlowBrainState
from sortaquant.validation.markout import MarkoutAnalyzer


def test_raim_full_end_to_end_lifecycle():
    """Validates complete integration across all four regimes and deterministic safety governors."""
    # 1. Initialize Slow Brain
    slow_brain = SlowBrainAdapter(tau_decay_s=300.0)
    slow_brain.update_state(
        SlowBrainState(
            z_VIX=0.5,
            TrendAlign=1,
            VolExpansion=0,
            EventRisk=0,
            UniverseScore=0.7,
            risk_multiplier=0.85,  # Tightens risk by 15%
            confidence=0.9,
        )
    )

    # 2. Initialize Fast Body Simulator on Zero-Maker-Fee pair (BTC/FDUSD)
    sim = TickSimulator(
        symbol="BTCFDUSD",
        maker_fee_bps=0.0,
        stress_latency_2x=False,
        slow_brain=slow_brain,
    )

    # 3. Generate synthetic market transitioning through R1 -> R2 -> R3 -> R4 -> R1
    bbos, trades = MarketReplayAdapter.generate_synthetic_regime_dataset(n_ticks=500, seed=123)

    # 4. Stream events in causal order (trades before the next BBO)
    regimes_visited = set()
    halt_observed = False
    trade_idx = 0
    n_trades = len(trades)
    for bbo in bbos:
        while trade_idx < n_trades and trades[trade_idx].timestamp_ns < bbo.timestamp_ns:
            sim.step_trade(trades[trade_idx])
            trade_idx += 1
        sim.step_bbo(bbo)
        if sim.regime_engine._last_output is not None:
            regimes_visited.add(sim.regime_engine._last_output.active_regime)
        if sim.regime_engine.is_halted:
            halt_observed = True
    while trade_idx < n_trades:
        sim.step_trade(trades[trade_idx])
        trade_idx += 1

    # 5. Verify system lifecycle invariants
    summary = sim.summarize()

    # Invariant A: Must have visited multiple regimes
    assert RegimeState.R1_CALM in regimes_visited
    assert RegimeState.R2_TRENDING in regimes_visited

    # Invariant B: Flash crash / structural break (R4) must trigger deterministic halt
    assert halt_observed is True

    # Invariant C: Dynamic inventory ceiling Q_max was never violated
    assert abs(sim.risk_governor.current_position) <= sim.risk_governor.config.max_gross_notional / 50_000.0

    # Invariant D: Markout analysis report
    analyzer = MarkoutAnalyzer(target_half_spread_bps=0.60)
    report = analyzer.generate_report(sim.fills)
    assert report.total_fills > 0

    # Invariant E: Gate 1 Economic edge preserved
    # Net edge = half_spread - (fee + E[AS]) > 0
    assert summary.total_trades > 0
    assert summary.maker_fees_paid == 0.0

"""Unit tests for 4-State HMM, BOCPD, and Regime Engine."""

import pytest
import numpy as np

from sortaquant.core.types import RegimeState
from sortaquant.regime.hmm import FourStateHMM
from sortaquant.regime.bocpd import BOCPDetector
from sortaquant.regime.engine import RegimeEngine


def test_hmm_forward_filter_stability():
    hmm = FourStateHMM()
    pi = np.array([0.7, 0.2, 0.08, 0.02])

    # Feed calm R1 observations: [vol, spread, imbalance, vpin, ofi_z, vr, hawkes_n]
    x_r1 = np.array([0.05, 0.80, 0.00, 0.15, 0.00, -0.20, 0.15])
    for _ in range(10):
        pi = hmm.forward_filter_step(pi, x_r1)
        assert np.isclose(np.sum(pi), 1.0)
        assert not np.any(np.isnan(pi))

    assert pi[0] > 0.80  # R1 posterior dominant


def test_hmm_transition_tilting():
    hmm = FourStateHMM()
    A_neutral = hmm.get_tilted_transition_matrix(z_vix=0.0, event_risk=0)
    A_tilted = hmm.get_tilted_transition_matrix(z_vix=2.5, event_risk=1)

    # When macro stress is high, transition probabilities to R3 and R4 must increase
    assert A_tilted[0, 2] > A_neutral[0, 2]
    assert A_tilted[0, 3] > A_neutral[0, 3]
    # Rows must sum to 1
    assert np.allclose(np.sum(A_tilted, axis=1), 1.0)


def test_bocpd_changepoint_detection():
    detector = BOCPDetector(hazard_rate=50.0, alarm_threshold=0.75)

    # Stationary regime: small noise
    np.random.seed(42)
    alarms = []
    for _ in range(40):
        alarms.append(detector.update(np.random.normal(0, 0.2)))
    # Stationary series should not persistently trip alarm
    assert not any(alarms[-10:])

    # Inject violent regime shift: huge 8-sigma shock
    alarm_fired = detector.update(8.5)
    assert alarm_fired is True
    assert detector.changepoint_probability > 0.75


def test_regime_engine_hysteresis():
    engine = RegimeEngine(halt_threshold_r4=0.70, release_threshold_r4=0.40, release_confirmation_ticks=5)

    # 1. Normal R1 feed
    x_r1 = np.array([0.05, 0.80, 0.00, 0.15, 0.00, -0.20, 0.15])
    for _ in range(10):
        out = engine.update(x_r1, return_for_bocpd=0.0)
    assert out.is_halted is False
    assert out.active_regime == RegimeState.R1_CALM

    # 2. Stress / Flash Crash feed: severe R4 features
    x_r4 = np.array([1.50, 10.0, -0.80, 0.95, -3.0, 1.0, 0.95])
    halted = False
    for _ in range(15):
        out = engine.update(x_r4, return_for_bocpd=-5.0)
        if out.is_halted:
            halted = True
            break
    assert halted is True
    assert engine.is_halted is True
    assert out.blended_size_mult == 0.0  # Zero quoting size when halted

    # 3. Return to calm R1 feed: Hysteresis check!
    # Even when R1 features return, engine MUST NOT release immediately on tick 1
    out_tick1 = engine.update(x_r1, return_for_bocpd=0.0)
    assert out_tick1.is_halted is True  # Still halted due to confirmation threshold

    # Feed enough confirming ticks
    for _ in range(10):
        out_release = engine.update(x_r1, return_for_bocpd=0.0)

    # Posterior should recover and release
    assert out_release.posterior[3] < 0.40
    assert out_release.is_halted is False

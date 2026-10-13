"""Unit tests for Gate 1 Hurdle Validator."""

import pytest
from sortaquant.validation.gate1 import Gate1HurdleValidator, RollingGate1Monitor


def test_gate1_passes_when_costs_below_half_spread():
    # maker_fee = 0.0 bps, AS = 0.2 bps, spread = 1.0 bps -> half spread = 0.5 bps
    # total cost = 0.2 < 0.5 -> PASSED
    res = Gate1HurdleValidator.evaluate(
        maker_fee_bps=0.0,
        expected_as_bps=0.2,
        typical_spread_bps=1.0,
        instrument="BTC/FDUSD",
        venue="Binance",
    )
    assert res.passed is True
    assert res.net_edge_bps == pytest.approx(0.3)
    assert res.total_hurdle_cost_bps == pytest.approx(0.2)
    assert res.half_spread_bps == pytest.approx(0.5)


def test_gate1_fails_when_retail_fees_exceed_half_spread():
    # Binance Spot standard VIP 0: maker_fee = 7.5 bps, spread = 0.0015 bps
    # half spread = 0.00075 bps -> FAILS severely
    res = Gate1HurdleValidator.evaluate(
        maker_fee_bps=7.5,
        expected_as_bps=0.01,
        typical_spread_bps=0.0015,
        instrument="BTC/USDT",
        venue="Binance Spot",
    )
    assert res.passed is False
    assert res.net_edge_bps < 0.0
    assert "FAILED" in res.message


def test_gate1_audit_venues():
    audit = Gate1HurdleValidator.audit_venues()
    assert audit["binance_spot_btcusdt_vip0"].passed is False
    assert audit["binance_spot_btcfdusd_zerofee"].passed is True
    assert audit["binance_perp_btcusdt_vip0"].passed is False
    assert audit["binance_perp_btcusdt_mm_rebate"].passed is True


def test_rolling_gate1_monitor():
    monitor = RollingGate1Monitor(maker_fee_bps=0.0, window_size=10, instrument="BTC/FDUSD")
    # Feed tight spreads with low AS
    for _ in range(15):
        monitor.record_observation(spread_bps=1.2, realized_as_bps=0.25)

    health = monitor.check_health()
    assert health.passed is True
    assert health.net_edge_bps > 0

    # Feed toxic markouts that degrade performance
    for _ in range(15):
        monitor.record_observation(spread_bps=1.0, realized_as_bps=0.8)

    toxic_health = monitor.check_health()
    assert toxic_health.passed is False

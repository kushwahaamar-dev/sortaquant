"""Unit tests for Deterministic Risk Governor and Hard Limits."""

import pytest

from sortaquant.core.types import BBO, Fill, QuotePair, RegimeState, Side
from sortaquant.risk.limits import RiskLimitsConfig, DynamicInventoryLimit
from sortaquant.risk.watchdog import StaleFeedWatchdog
from sortaquant.risk.rate_limiter import TokenBucketRateLimiter
from sortaquant.risk.governor import DeterministicRiskGovernor


def test_stale_feed_watchdog_timeout():
    # 500ms timeout = 500_000_000 ns
    wd = StaleFeedWatchdog(timeout_ns=500_000_000)

    wd.on_packet(1_000_000_000)
    # Check at +200ms -> healthy
    assert wd.check(1_200_000_000) is True

    # Check at +600ms -> STALE!
    assert wd.check(1_600_000_000) is False
    assert wd.is_stale is True

    # New packet recovers watchdog
    wd.on_packet(1_650_000_000)
    assert wd.check(1_660_000_000) is True


def test_token_bucket_rate_limiter_and_backoff():
    limiter = TokenBucketRateLimiter(max_tokens=3.0, refill_rate_per_sec=1.0)
    t0 = 1_000_000_000

    # Consume 3 tokens
    assert limiter.consume(t0) is True
    assert limiter.consume(t0) is True
    assert limiter.consume(t0) is True
    # 4th token fails
    assert limiter.consume(t0) is False

    # After reject, backoff increases
    limiter.on_reject()
    assert limiter.backoff_factor == 2.0
    limiter.on_reject()
    assert limiter.backoff_factor == 4.0


def test_dynamic_inventory_limit_calculation():
    config = RiskLimitsConfig(
        capital_budget=100_000.0,
        z_score=2.58,
        h_unwind_s=25.0,
        depth_fraction=0.10,
        max_gross_notional=50_000.0,
    )
    dil = DynamicInventoryLimit(config)

    # mid=50000, vol=0.01, depth=20
    # var_limit = 100000 / (2.58 * 0.01 * 5) = 100000 / 0.129 = 775193 USD -> 15.5 BTC
    # depth_limit = 0.10 * 20 = 2.0 BTC
    # gross_limit = 50000 / 50000 = 1.0 BTC
    # min is gross_limit = 1.0 BTC
    q_max = dil.compute_q_max(mid_price=50000.0, volatility=0.01, l2_depth_volume=20.0)
    assert q_max == pytest.approx(1.0)


def test_risk_governor_asymmetric_llm_rule():
    governor = DeterministicRiskGovernor()
    now_ns = 1_000_000_000
    governor.on_feed_packet(now_ns)

    quotes = QuotePair(
        bid_price=50000.0,
        bid_size=1.0,
        ask_price=50010.0,
        ask_size=1.0,
        fair_value=50005.0,
        regime=RegimeState.R1_CALM,
        timestamp_ns=now_ns,
    )

    # 1. LLM attempts to loosen risk by recommending m = 2.5
    # The governor must CLAMP m to 1.0 (rules limit)!
    gq_loose = governor.evaluate_quotes(
        quotes=quotes,
        mid_price=50005.0,
        volatility=0.001,
        l2_depth=100.0,
        now_ns=now_ns,
        llm_risk_multiplier=2.5,
    )
    assert gq_loose.applied_risk_multiplier == 1.0
    assert gq_loose.bid_size == pytest.approx(1.0)

    # 2. LLM recommends tightening risk with m = 0.4
    # The governor honors the tightening!
    gq_tight = governor.evaluate_quotes(
        quotes=quotes,
        mid_price=50005.0,
        volatility=0.001,
        l2_depth=100.0,
        now_ns=now_ns,
        llm_risk_multiplier=0.4,
    )
    assert gq_tight.applied_risk_multiplier == 0.4
    assert gq_tight.bid_size == pytest.approx(0.4)


def test_risk_governor_position_clamping():
    config = RiskLimitsConfig(
        capital_budget=10_000.0,
        depth_fraction=0.05,
        max_gross_notional=10_000.0,
    )
    governor = DeterministicRiskGovernor(config)
    now_ns = 1_000_000_000
    governor.on_feed_packet(now_ns)

    # Fill long position of +2.0 BTC
    governor.on_fill(
        Fill(
            fill_id="1",
            order_id="1",
            symbol="BTCUSDT",
            side=Side.BUY,
            price=50000.0,
            size=2.0,
            fee=0.0,
            is_maker=True,
            timestamp_ns=now_ns,
            mid_at_fill=50000.0,
        ),
        current_mid=50000.0,
    )

    quotes = QuotePair(
        bid_price=50000.0,
        bid_size=1.0,
        ask_price=50010.0,
        ask_size=1.0,
        fair_value=50005.0,
        regime=RegimeState.R1_CALM,
        timestamp_ns=now_ns,
    )

    # Suppose Q_max evaluates to 2.0. Since position is already 2.0, bid must be cancelled!
    gq = governor.evaluate_quotes(
        quotes=quotes,
        mid_price=50005.0,
        volatility=0.01,
        l2_depth=20.0,  # 0.05 * 20 = 1.0 -> q_max will be <= 1.0 or 2.0
        now_ns=now_ns,
    )
    assert gq.bid_price is None  # Bid cancelled because long inventory exceeded Q_max
    assert gq.bid_size == 0.0
    assert gq.ask_price is not None  # Ask remains active to offload inventory!


def test_risk_governor_drawdown_halt():
    config = RiskLimitsConfig(capital_budget=1000.0, max_drawdown_pct=0.05)  # 5% max drawdown
    governor = DeterministicRiskGovernor(config)
    now_ns = 1_000_000_000
    governor.on_feed_packet(now_ns)

    # Cash ledger down 10% with flat inventory
    governor.cash = 900.0

    quotes = QuotePair(
        bid_price=100.0,
        bid_size=1.0,
        ask_price=101.0,
        ask_size=1.0,
        fair_value=100.5,
        regime=RegimeState.R1_CALM,
        timestamp_ns=now_ns,
    )

    gq = governor.evaluate_quotes(
        quotes=quotes,
        mid_price=100.0,
        volatility=0.01,
        l2_depth=100.0,
        now_ns=now_ns,
    )
    assert gq.is_halted is True
    assert gq.bid_price is None
    assert gq.ask_price is None


def test_cash_ledger_books_notional_and_fees():
    governor = DeterministicRiskGovernor(RiskLimitsConfig(capital_budget=100_000.0))
    now_ns = 1_000_000_000
    governor.on_fill(
        Fill(
            fill_id="1",
            order_id="1",
            symbol="BTCFDUSD",
            side=Side.BUY,
            price=50_000.0,
            size=1.0,
            fee=5.0,
            is_maker=True,
            timestamp_ns=now_ns,
            mid_at_fill=50_000.0,
        ),
        current_mid=50_000.0,
    )
    assert governor.cash == pytest.approx(100_000.0 - 50_000.0 - 5.0)
    assert governor.current_position == pytest.approx(1.0)
    assert governor.equity(50_100.0) == pytest.approx(governor.cash + 50_100.0)


def test_adverse_markout_streak_pauses_quotes():
    config = RiskLimitsConfig(max_loss_streak=2)
    governor = DeterministicRiskGovernor(config)
    now_ns = 1_000_000_000
    governor.on_feed_packet(now_ns)
    governor.on_adverse_markout(now_ns)
    governor.on_adverse_markout(now_ns + 1)
    assert governor.is_streak_paused is True

    quotes = QuotePair(
        bid_price=100.0,
        bid_size=1.0,
        ask_price=101.0,
        ask_size=1.0,
        fair_value=100.5,
        regime=RegimeState.R1_CALM,
        timestamp_ns=now_ns,
    )
    gq = governor.evaluate_quotes(
        quotes=quotes,
        mid_price=100.0,
        volatility=0.01,
        l2_depth=100.0,
        now_ns=now_ns + 2,
    )
    assert gq.is_halted is True
    assert gq.bid_price is None

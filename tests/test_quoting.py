"""Unit tests for Inventory-Aware and Regime-Adaptive Quoting Engine."""

import pytest
import numpy as np

from sortaquant.core.types import BBO, RegimeState
from sortaquant.quoting.microprice import FairValueEstimator
from sortaquant.quoting.gueant_lehalle import GueantLehalleModel
from sortaquant.quoting.engine import QuotingEngine
from sortaquant.regime.engine import RegimeOutput


def test_microprice_and_fair_value_estimator():
    fve = FairValueEstimator(beta_ofi=0.1)

    # Balanced book
    bbo_bal = BBO(bid_price=100.0, bid_size=10.0, ask_price=102.0, ask_size=10.0, timestamp_ns=1000)
    assert fve.compute_micro_price(bbo_bal) == 101.0

    # Imbalanced book: heavy bid (size 30 vs 10) -> microprice shifts towards ask (buyers pushing)
    bbo_heavy_bid = BBO(bid_price=100.0, bid_size=30.0, ask_price=102.0, ask_size=10.0, timestamp_ns=1000)
    # microprice = 101 + ((30 - 10) / 40) * (2 / 2) = 101 + 0.5 * 1 = 101.5
    assert fve.compute_micro_price(bbo_heavy_bid) == pytest.approx(101.5)

    # Positive OFI drift
    fv = fve.estimate_fair_value(bbo_heavy_bid, ofi_value=5.0)
    assert fv > 101.5  # Positive drift


def test_gueant_lehalle_inventory_skew():
    model = GueantLehalleModel(base_gamma=0.1, k=1.5)

    # Neutral inventory (q = 0)
    q0 = model.compute_quotes(fair_value=100.0, inventory=0.0, volatility=0.01, gamma=0.1)
    assert q0.bid_half_spread == pytest.approx(q0.ask_half_spread)
    assert q0.reservation_price == pytest.approx(100.0)

    # Long inventory (q = +2.0): wants to offload
    # Reservation price must drop below fair value
    q_long = model.compute_quotes(fair_value=100.0, inventory=2.0, volatility=0.01, gamma=0.1)
    assert q_long.reservation_price < 100.0
    # Ask half-spread must be tighter (to get filled on ask)
    assert q_long.ask_half_spread < q0.ask_half_spread
    # Bid half-spread must be wider (to avoid buying more)
    assert q_long.bid_half_spread > q0.bid_half_spread

    # Short inventory (q = -2.0): wants to buy back
    q_short = model.compute_quotes(fair_value=100.0, inventory=-2.0, volatility=0.01, gamma=0.1)
    assert q_short.reservation_price > 100.0
    assert q_short.bid_half_spread < q0.bid_half_spread
    assert q_short.ask_half_spread > q0.ask_half_spread


def test_quoting_engine_regime_adaptations():
    engine = QuotingEngine(
        symbol="BTCUSDT",
        base_gamma=0.15,
        base_quote_size=1.0,
        tick_size=0.01,
        lot_size=0.001,
    )
    bbo = BBO(bid_price=50000.0, bid_size=5.0, ask_price=50002.0, ask_size=5.0, timestamp_ns=1_000_000)

    # Case 1: R1 (Calm)
    r1_out = RegimeOutput(
        active_regime=RegimeState.R1_CALM,
        posterior=np.array([0.9, 0.05, 0.04, 0.01]),
        is_halted=False,
        bocpd_alarm=False,
        blended_risk_aversion_mult=1.0,
        blended_spread_mult=1.0,
        blended_size_mult=1.0,
        reason="NORMAL",
    )
    q_r1 = engine.generate_quotes(bbo, inventory=0.0, volatility=0.001, regime_output=r1_out)
    assert q_r1.bid_price is not None
    assert q_r1.ask_price is not None
    assert q_r1.bid_price < q_r1.ask_price
    assert q_r1.bid_price <= bbo.bid_price
    assert q_r1.ask_price >= bbo.ask_price
    assert q_r1.bid_size == pytest.approx(1.0)
    assert q_r1.ask_size == pytest.approx(1.0)

    # Case 2: R3 (High-Vol Stress) -> Spreads widen, sizes shrink
    r3_out = RegimeOutput(
        active_regime=RegimeState.R3_HIGHVOL,
        posterior=np.array([0.05, 0.1, 0.8, 0.05]),
        is_halted=False,
        bocpd_alarm=False,
        blended_risk_aversion_mult=3.0,
        blended_spread_mult=2.5,
        blended_size_mult=0.35,
        reason="HIGHVOL",
    )
    q_r3 = engine.generate_quotes(bbo, inventory=0.0, volatility=0.005, regime_output=r3_out)
    spread_r1 = q_r1.ask_price - q_r1.bid_price
    spread_r3 = q_r3.ask_price - q_r3.bid_price
    assert spread_r3 > spread_r1
    assert q_r3.bid_size < q_r1.bid_size

    # Case 3: R4 (Flash Crash / Halt) -> Quotes pulled completely!
    r4_out = RegimeOutput(
        active_regime=RegimeState.R4_BREAK,
        posterior=np.array([0.01, 0.01, 0.1, 0.88]),
        is_halted=True,
        bocpd_alarm=True,
        blended_risk_aversion_mult=6.0,
        blended_spread_mult=5.0,
        blended_size_mult=0.0,
        reason="HALT: pi(R4) > 0.70",
    )
    q_r4 = engine.generate_quotes(bbo, inventory=0.0, volatility=0.02, regime_output=r4_out)
    assert q_r4.bid_price is None
    assert q_r4.ask_price is None
    assert q_r4.bid_size == 0.0
    assert q_r4.ask_size == 0.0

"""Unit tests for microstructure feature extractors."""

import pytest
import numpy as np

from sortaquant.core.types import BBO, Side, Trade
from sortaquant.features.bipower import BipowerVariationEstimator
from sortaquant.features.ofi import OFIEstimator
from sortaquant.features.vpin import VPINEstimator
from sortaquant.features.variance_ratio import VarianceRatioEstimator
from sortaquant.features.hawkes import HawkesIntensityEstimator
from sortaquant.features import MicrostructureFeatureEngine


def test_bipower_variation_continuous_vs_jump():
    bve = BipowerVariationEstimator(window_size=50)

    # Generate smooth small returns
    np.random.seed(42)
    p = 100.0
    for _ in range(40):
        ret = np.random.normal(0, 0.001)
        p *= np.exp(ret)
        bve.update(p)

    bv_vol, rv_vol, jump_ratio = bve.compute()
    assert bv_vol > 0.0
    assert rv_vol > 0.0
    assert jump_ratio < 0.5  # mostly continuous

    # Now inject a massive discrete jump (e.g. 5% in a single tick)
    p *= 1.05
    bve.update(p)
    bv_vol_post, rv_vol_post, jump_ratio_post = bve.compute()
    # Jump ratio increases significantly because single jump inflates RV much more than BV
    assert rv_vol_post > rv_vol
    assert jump_ratio_post > jump_ratio


def test_ofi_directional_impact():
    ofi = OFIEstimator(window_size=20)

    # Initial BBO
    bbo0 = BBO(bid_price=100.0, bid_size=10.0, ask_price=101.0, ask_size=10.0, timestamp_ns=1000)
    assert ofi.update_bbo(bbo0) == 0.0

    # Bid price moves up from 100 to 100.5 with size 15 (aggressive bid demand)
    bbo1 = BBO(bid_price=100.5, bid_size=15.0, ask_price=101.0, ask_size=10.0, timestamp_ns=2000)
    val1 = ofi.update_bbo(bbo1)
    assert val1 > 0.0  # Positive OFI
    assert val1 == 15.0

    # Ask price moves down from 101.0 to 100.8 with size 20 (aggressive supply)
    bbo2 = BBO(bid_price=100.5, bid_size=15.0, ask_price=100.8, ask_size=20.0, timestamp_ns=3000)
    val2 = ofi.update_bbo(bbo2)
    assert val2 < 0.0  # Negative OFI


def test_vpin_toxicity_discrimination():
    # Symmetric balanced trade flow: should have lower VPIN
    vpin_sym = VPINEstimator(bucket_size=10.0, num_buckets=20)
    for i in range(100):
        side = Side.BUY if i % 2 == 0 else Side.SELL
        vpin_sym.add_trade(Trade(price=100.0, size=5.0, side=side, timestamp_ns=i * 1000))

    score_sym = vpin_sym.vpin

    # Extreme toxic one-sided buying
    vpin_toxic = VPINEstimator(bucket_size=10.0, num_buckets=20)
    for i in range(100):
        vpin_toxic.add_trade(Trade(price=100.0 + i * 0.1, size=5.0, side=Side.BUY, timestamp_ns=i * 1000))

    score_toxic = vpin_toxic.vpin
    assert score_toxic > score_sym
    assert score_toxic == pytest.approx(1.0, abs=0.05)


def test_variance_ratio_mean_reversion_vs_trend():
    vre = VarianceRatioEstimator(window_size=100)

    # 1. Perfectly mean-reverting oscillating sequence: 100, 101, 100, 101...
    for i in range(80):
        vre.update(100.0 + (1.0 if i % 2 == 0 else 0.0))
    vr_mr = vre.compute(horizon=4)
    assert vr_mr < 1.0  # Mean reversion confirmed!

    # 2. Trending persistent sequence: 100, 101, 102, 103...
    vre_trend = VarianceRatioEstimator(window_size=100)
    for i in range(80):
        vre_trend.update(100.0 + i * 0.5)
    vr_trend = vre_trend.compute(horizon=4)
    assert vr_trend > 1.0  # Momentum confirmed!


def test_hawkes_branching_ratio_bounds():
    hawkes = HawkesIntensityEstimator(window_size=60)
    # Feed periodic timestamps
    for i in range(50):
        val = hawkes.update(timestamp_ns=i * 100_000_000)
        assert 0.0 < val < 1.0


def test_microstructure_feature_engine():
    engine = MicrostructureFeatureEngine("BTCUSDT")

    # Feed trades
    for i in range(20):
        engine.on_trade(Trade(price=50000.0, size=1.0, side=Side.BUY, timestamp_ns=i * 1_000_000))

    # Feed BBO
    bbo = BBO(bid_price=49995.0, bid_size=2.0, ask_price=50005.0, ask_size=3.0, timestamp_ns=20_000_000)
    feats = engine.on_bbo(bbo)

    assert feats.mid_price == 50000.0
    assert feats.spread_bps == pytest.approx((10.0 / 50000.0) * 10_000)
    assert feats.book_imbalance == pytest.approx((2.0 - 3.0) / 5.0)
    vec = feats.to_vector()
    assert len(vec) == 7
    assert not np.any(np.isnan(vec))

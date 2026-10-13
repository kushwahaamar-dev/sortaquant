"""Unit tests for L2 Order Book."""

import pytest
from sortaquant.core.orderbook import OrderBookL2
from sortaquant.core.types import Side


def test_orderbook_snapshot_and_bbo():
    ob = OrderBookL2("BTCUSDT")
    bids = [(65000.0, 1.5), (64990.0, 2.0), (64980.0, 5.0)]
    asks = [(65010.0, 1.0), (65020.0, 3.0), (65030.0, 4.0)]

    ob.set_snapshot(bids, asks, timestamp_ns=1000)
    bbo = ob.get_bbo()

    assert bbo is not None
    assert bbo.bid_price == 65000.0
    assert bbo.bid_size == 1.5
    assert bbo.ask_price == 65010.0
    assert bbo.ask_size == 1.0
    assert bbo.mid_price == 65005.0
    assert bbo.spread == 10.0
    assert bbo.relative_spread_bps == pytest.approx((10.0 / 65005.0) * 10_000)


def test_orderbook_delta_updates():
    ob = OrderBookL2("BTCUSDT")
    ob.update_level(Side.BUY, 65000.0, 1.0, timestamp_ns=100)
    ob.update_level(Side.SELL, 65005.0, 1.0, timestamp_ns=100)

    # Modify bid size
    ob.update_level(Side.BUY, 65000.0, 2.5, timestamp_ns=200)
    bbo = ob.get_bbo()
    assert bbo.bid_size == 2.5

    # Remove bid level by setting size to 0
    ob.update_level(Side.BUY, 65000.0, 0.0, timestamp_ns=300)
    assert ob.get_bbo() is None  # no bids remaining


def test_orderbook_depth_and_imbalance():
    ob = OrderBookL2("BTCUSDT")
    bids = [(100.0, 10.0), (99.0, 5.0)]
    asks = [(101.0, 5.0), (102.0, 5.0)]
    ob.set_snapshot(bids, asks)

    bid_vol, ask_vol = ob.get_depth_volume(levels=2)
    assert bid_vol == 15.0
    assert ask_vol == 10.0

    # Imbalance: (15 - 10) / (15 + 10) = 5 / 25 = 0.2
    imb = ob.get_imbalance(levels=2)
    assert imb == pytest.approx(0.2)


def test_orderbook_micro_price():
    ob = OrderBookL2("BTCUSDT")
    # bid=100 (size 3), ask=102 (size 1) -> spread=2, total=4
    # microprice = 100 + (3 / 4) * 2 = 100 + 1.5 = 101.5
    ob.set_snapshot([(100.0, 3.0)], [(102.0, 1.0)])
    assert ob.get_micro_price() == pytest.approx(101.5)

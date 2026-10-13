"""Unit tests for realistic tick-level simulator and queue position mechanics."""

import pytest

from sortaquant.core.types import BBO, Side, Trade
from sortaquant.sim.queue_model import OrderQueueTracker
from sortaquant.sim.latency_model import LatencyModel
from sortaquant.sim.simulator import TickSimulator


def test_queue_tracker_fifo_priority_and_matching():
    tracker = OrderQueueTracker()
    t0 = 1_000_000_000

    # Place buy limit order at $100 for size 2.0 with existing ahead depth 5.0
    order = tracker.place_order(
        order_id="b1",
        side=Side.BUY,
        price=100.0,
        size=2.0,
        existing_level_depth=5.0,
        timestamp_ns=t0,
    )
    assert order.ahead_volume == 5.0
    assert order.remaining_size == 2.0

    # Incoming SELL trade at $100 for size 3.0 (smaller than ahead volume 5.0)
    # Ahead volume absorbs trade. Our order should NOT get filled!
    fills1 = tracker.on_market_trade(trade_price=100.0, trade_size=3.0, trade_side=Side.SELL)
    assert len(fills1) == 0
    assert order.ahead_volume == 2.0
    assert order.remaining_size == 2.0

    # Incoming SELL trade at $100 for size 3.0 (exceeds remaining ahead volume 2.0)
    # Ahead volume drops to 0, excess 1.0 fills half of our order!
    fills2 = tracker.on_market_trade(trade_price=100.0, trade_size=3.0, trade_side=Side.SELL)
    assert len(fills2) == 1
    assert fills2[0] == ("b1", 1.0)
    assert order.ahead_volume == 0.0
    assert order.remaining_size == 1.0

    # Another trade finishes the remaining 1.0
    fills3 = tracker.on_market_trade(trade_price=100.0, trade_size=2.0, trade_side=Side.SELL)
    assert len(fills3) == 1
    assert fills3[0] == ("b1", 1.0)
    assert tracker.get_order("b1") is None  # Fully filled and removed


def test_queue_tracker_cancel_degradation():
    tracker = OrderQueueTracker()
    tracker.place_order("b2", Side.BUY, price=100.0, size=1.0, existing_level_depth=10.0, timestamp_ns=1000)
    order = tracker.get_order("b2")
    assert order.ahead_volume == 10.0

    # Market participants cancel 4.0 volume at level
    tracker.on_level_cancellation(price=100.0, side=Side.BUY, cancel_volume=4.0)
    assert order.ahead_volume == 6.0


def test_latency_model_2x_stress():
    lat_normal = LatencyModel(median_latency_ms=5.0, stress_2x=False, seed=42)
    lat_stressed = LatencyModel(median_latency_ms=5.0, stress_2x=True, seed=42)

    s1 = lat_normal.sample_latency_ms()
    s2 = lat_stressed.sample_latency_ms()
    assert s2 == pytest.approx(s1 * 2.0)


def test_tick_simulator_end_to_end_stepping():
    sim = TickSimulator(symbol="BTCUSDT", maker_fee_bps=0.0, stress_latency_2x=False)

    # Step through BBO ticks
    base_t = 1_000_000_000
    for i in range(20):
        t_ns = base_t + i * 100_000_000  # 100ms intervals
        bbo = BBO(
            bid_price=50000.0 + (i % 2) * 0.1,
            bid_size=10.0,
            ask_price=50001.0 + (i % 2) * 0.1,
            ask_size=10.0,
            timestamp_ns=t_ns,
        )
        sim.step_bbo(bbo)

        # Inject periodic trades
        if i % 3 == 0:
            trade = Trade(
                price=50000.0,
                size=20.0,  # Large enough to clear ahead queue and fill resting bid
                side=Side.SELL,
                timestamp_ns=t_ns + 5_000_000,
            )
            sim.step_trade(trade)

    summary = sim.summarize()
    assert summary.total_trades > 0
    assert summary.total_volume > 0
    assert summary.maker_fees_paid == 0.0  # Zero maker fee promo verified

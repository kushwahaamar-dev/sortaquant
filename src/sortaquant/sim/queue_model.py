"""FIFO Order Book Queue Position Modeling with Proportional Cancel Degradation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from sortaquant.core.types import Side


@dataclass
class QueueOrder:
    order_id: str
    side: Side
    price: float
    size: float
    ahead_volume: float
    behind_volume: float
    remaining_size: float
    created_at_ns: int
    matchable_at_ns: int = 0


class OrderQueueTracker:
    """Simulates realistic queue priority in a Level 2 FIFO limit order book."""

    def __init__(self):
        self._orders: dict[str, QueueOrder] = {}

    def place_order(
        self,
        order_id: str,
        side: Side,
        price: float,
        size: float,
        existing_level_depth: float,
        timestamp_ns: int,
        ack_delay_ns: int = 0,
    ) -> QueueOrder:
        """Adds a limit order to the back of the queue at the specified price level."""
        order = QueueOrder(
            order_id=order_id,
            side=side,
            price=price,
            size=size,
            ahead_volume=max(0.0, existing_level_depth),
            behind_volume=0.0,
            remaining_size=size,
            created_at_ns=timestamp_ns,
            matchable_at_ns=timestamp_ns + max(0, ack_delay_ns),
        )
        self._orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> Optional[QueueOrder]:
        return self._orders.pop(order_id, None)

    def on_level_cancellation(self, price: float, side: Side, cancel_volume: float) -> None:
        """Models queue position degradation when external market participants cancel orders.

        Cancels are assumed distributed proportionally across ahead and behind volume.
        """
        if cancel_volume <= 0:
            return

        for order in self._orders.values():
            if order.side == side and abs(order.price - price) < 1e-6:
                tot_competing = order.ahead_volume + order.behind_volume
                if tot_competing > 1e-9:
                    frac_ahead = order.ahead_volume / tot_competing
                    ahead_degraded = cancel_volume * frac_ahead
                    order.ahead_volume = max(0.0, order.ahead_volume - ahead_degraded)
                    order.behind_volume = max(0.0, order.behind_volume - (cancel_volume - ahead_degraded))
                else:
                    order.ahead_volume = max(0.0, order.ahead_volume - cancel_volume)

    def on_market_trade(
        self,
        trade_price: float,
        trade_size: float,
        trade_side: Side,
        trade_time_ns: Optional[int] = None,
    ) -> list[Tuple[str, float]]:
        """Matches incoming trade volume against queue ahead volume and our orders.

        Returns list of (order_id, fill_size). Orders are ignored until matchable_at_ns.
        """
        fills: list[Tuple[str, float]] = []
        remaining = trade_size

        for order_id, order in list(self._orders.items()):
            if trade_time_ns is not None and trade_time_ns < order.matchable_at_ns:
                continue

            matches = False
            if trade_side == Side.BUY and order.side == Side.SELL:
                if trade_price >= order.price - 1e-6:
                    matches = True
            elif trade_side == Side.SELL and order.side == Side.BUY:
                if trade_price <= order.price + 1e-6:
                    matches = True

            if not matches or remaining <= 1e-12:
                continue

            if remaining <= order.ahead_volume:
                order.ahead_volume -= remaining
                remaining = 0.0
            else:
                excess = remaining - order.ahead_volume
                order.ahead_volume = 0.0
                fill_size = min(order.remaining_size, excess)
                order.remaining_size -= fill_size
                remaining -= fill_size
                fills.append((order_id, fill_size))

                if order.remaining_size <= 1e-9:
                    del self._orders[order_id]

        return fills

    def get_order(self, order_id: str) -> Optional[QueueOrder]:
        return self._orders.get(order_id)

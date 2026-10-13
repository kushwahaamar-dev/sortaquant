"""Level 2 Limit Order Book with microsecond depth and imbalance calculation."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from sortaquant.core.types import BookLevel, BBO, Side


class OrderBookL2:
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self._bids: Dict[float, float] = {}  # price -> size
        self._asks: Dict[float, float] = {}  # price -> size
        self._last_update_ns: int = 0
        self._sequence_id: int = 0

    @property
    def last_update_ns(self) -> int:
        return self._last_update_ns

    @property
    def sequence_id(self) -> int:
        return self._sequence_id

    def clear(self) -> None:
        self._bids.clear()
        self._asks.clear()

    def update_level(self, side: Side, price: float, size: float, timestamp_ns: int = 0) -> None:
        target = self._bids if side == Side.BUY else self._asks
        if size <= 1e-12:
            target.pop(price, None)
        else:
            target[price] = size
        if timestamp_ns > 0:
            self._last_update_ns = timestamp_ns

    def apply_delta(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        timestamp_ns: int = 0,
        sequence_id: int = 0,
    ) -> None:
        for p, s in bids:
            self.update_level(Side.BUY, p, s, timestamp_ns)
        for p, s in asks:
            self.update_level(Side.SELL, p, s, timestamp_ns)
        if timestamp_ns > 0:
            self._last_update_ns = timestamp_ns
        if sequence_id > 0:
            self._sequence_id = sequence_id

    def set_snapshot(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        timestamp_ns: int = 0,
        sequence_id: int = 0,
    ) -> None:
        self._bids.clear()
        self._asks.clear()
        self.apply_delta(bids, asks, timestamp_ns, sequence_id)

    def get_bids(self, levels: Optional[int] = None) -> List[BookLevel]:
        sorted_prices = sorted(self._bids.keys(), reverse=True)
        if levels is not None:
            sorted_prices = sorted_prices[:levels]
        return [BookLevel(price=p, size=self._bids[p]) for p in sorted_prices]

    def get_asks(self, levels: Optional[int] = None) -> List[BookLevel]:
        sorted_prices = sorted(self._asks.keys())
        if levels is not None:
            sorted_prices = sorted_prices[:levels]
        return [BookLevel(price=p, size=self._asks[p]) for p in sorted_prices]

    def get_bbo(self) -> Optional[BBO]:
        if not self._bids or not self._asks:
            return None
        best_bid = max(self._bids.keys())
        best_ask = min(self._asks.keys())
        return BBO(
            bid_price=best_bid,
            bid_size=self._bids[best_bid],
            ask_price=best_ask,
            ask_size=self._asks[best_ask],
            timestamp_ns=self._last_update_ns,
        )

    def get_depth_volume(self, levels: int = 5) -> Tuple[float, float]:
        bids = self.get_bids(levels)
        asks = self.get_asks(levels)
        bid_vol = sum(b.size for b in bids)
        ask_vol = sum(a.size for a in asks)
        return bid_vol, ask_vol

    def get_imbalance(self, levels: int = 5) -> float:
        bid_vol, ask_vol = self.get_depth_volume(levels)
        total = bid_vol + ask_vol
        if total <= 1e-12:
            return 0.0
        return (bid_vol - ask_vol) / total

    def get_micro_price(self) -> Optional[float]:
        bbo = self.get_bbo()
        if bbo is None:
            return None
        return bbo.micro_price

    def get_vwap_mid(self, notional: float) -> Optional[float]:
        if not self._bids or not self._asks:
            return None
        # Compute bid vwap
        cum_notional = 0.0
        bid_cash = 0.0
        for b in self.get_bids():
            level_val = b.price * b.size
            if cum_notional + level_val >= notional:
                rem = notional - cum_notional
                bid_cash += rem
                break
            bid_cash += level_val
            cum_notional += level_val

        # Compute ask vwap
        cum_notional = 0.0
        ask_cash = 0.0
        for a in self.get_asks():
            level_val = a.price * a.size
            if cum_notional + level_val >= notional:
                rem = notional - cum_notional
                ask_cash += rem
                break
            ask_cash += level_val
            cum_notional += level_val

        bbo = self.get_bbo()
        return bbo.mid_price if bbo else None

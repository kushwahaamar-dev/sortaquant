"""Volume-Synchronized Probability of Toxicity (VPIN) (Easley, Lopez de Prado, O'Hara 2012).

Measures order toxicity and adverse selection probability by tracking order imbalances in volume time.
"""

from __future__ import annotations

from typing import List, Optional
import numpy as np
from sortaquant.core.types import Side, Trade


class VPINBucket:
    def __init__(self, bucket_size: float):
        self.bucket_size = bucket_size
        self.buy_volume: float = 0.0
        self.sell_volume: float = 0.0

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume

    @property
    def imbalance(self) -> float:
        return abs(self.buy_volume - self.sell_volume)

    @property
    def is_full(self) -> bool:
        return self.total_volume >= self.bucket_size - 1e-9


class VPINEstimator:
    """Computes VPIN over volume-synchronized buckets."""

    def __init__(self, bucket_size: float = 10.0, num_buckets: int = 50):
        self.bucket_size = bucket_size
        self.num_buckets = num_buckets
        self._current_bucket = VPINBucket(bucket_size)
        self._completed_buckets: List[VPINBucket] = []
        self._last_price: Optional[float] = None
        self._last_side: Side = Side.BUY

    def add_trade(self, trade: Trade) -> Optional[float]:
        """Processes an incoming trade. Returns current VPIN if ready."""
        # Classify trade side via tick rule if not explicitly known
        trade_side = trade.side
        if trade_side is None:
            if self._last_price is not None:
                if trade.price > self._last_price:
                    trade_side = Side.BUY
                elif trade.price < self._last_price:
                    trade_side = Side.SELL
                else:
                    trade_side = self._last_side
            else:
                trade_side = Side.BUY

        self._last_price = trade.price
        self._last_side = trade_side

        remaining_vol = trade.size

        while remaining_vol > 1e-9:
            space_left = self.bucket_size - self._current_bucket.total_volume
            vol_to_add = min(remaining_vol, space_left)

            if trade_side == Side.BUY:
                self._current_bucket.buy_volume += vol_to_add
            else:
                self._current_bucket.sell_volume += vol_to_add

            remaining_vol -= vol_to_add

            if self._current_bucket.is_full:
                self._completed_buckets.append(self._current_bucket)
                if len(self._completed_buckets) > self.num_buckets:
                    self._completed_buckets.pop(0)
                self._current_bucket = VPINBucket(self.bucket_size)

        return self.vpin

    @property
    def ready(self) -> bool:
        return len(self._completed_buckets) >= min(10, self.num_buckets)

    @property
    def vpin(self) -> float:
        """Computes VPIN = sum(|V_B - V_S|) / (N * V)."""
        if not self._completed_buckets:
            return 0.0

        n = len(self._completed_buckets)
        tot_imbalance = sum(b.imbalance for b in self._completed_buckets)
        tot_volume = n * self.bucket_size

        if tot_volume <= 1e-9:
            return 0.0

        return float(min(1.0, max(0.0, tot_imbalance / tot_volume)))

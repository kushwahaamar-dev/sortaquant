"""Order Flow Imbalance (OFI) Estimator (Cont, Kukanov & Stoikov 2014).

Measures net shifts in limit order book supply and demand to predict short-horizon price impact.
"""

from __future__ import annotations

from typing import List, Optional, Tuple
import numpy as np
from sortaquant.core.types import BBO, BookLevel


class OFIEstimator:
    """Computes single-level and multi-level Order Flow Imbalance."""

    def __init__(self, window_size: int = 50, multi_level_decay: float = 0.5):
        self.window_size = window_size
        self.multi_level_decay = multi_level_decay
        self._prev_bbo: Optional[BBO] = None
        self._prev_bids: Optional[List[BookLevel]] = None
        self._prev_asks: Optional[List[BookLevel]] = None
        self._ofi_history: List[float] = []

    def update_bbo(self, current_bbo: BBO) -> float:
        """Computes Level-1 OFI between previous and current BBO."""
        if self._prev_bbo is None:
            self._prev_bbo = current_bbo
            return 0.0

        p_b = current_bbo.bid_price
        p_b_prev = self._prev_bbo.bid_price
        q_b = current_bbo.bid_size
        q_b_prev = self._prev_bbo.bid_size

        if p_b > p_b_prev:
            delta_b = q_b
        elif p_b == p_b_prev:
            delta_b = q_b - q_b_prev
        else:
            delta_b = -q_b_prev

        p_a = current_bbo.ask_price
        p_a_prev = self._prev_bbo.ask_price
        q_a = current_bbo.ask_size
        q_a_prev = self._prev_bbo.ask_size

        if p_a > p_a_prev:
            delta_a = -q_a_prev
        elif p_a == p_a_prev:
            delta_a = q_a - q_a_prev
        else:
            delta_a = q_a

        ofi = delta_b - delta_a
        self._prev_bbo = current_bbo
        self._ofi_history.append(ofi)
        if len(self._ofi_history) > self.window_size:
            self._ofi_history.pop(0)

        return ofi

    def update_multilevel(
        self,
        bids: List[BookLevel],
        asks: List[BookLevel],
        max_levels: int = 5,
    ) -> float:
        """Computes exponentially decayed multi-level OFI across top K levels."""
        if not bids or not asks:
            return 0.0

        if self._prev_bids is None or self._prev_asks is None:
            self._prev_bids = bids[:max_levels]
            self._prev_asks = asks[:max_levels]
            return 0.0

        total_ofi = 0.0
        n_levels = min(len(bids), len(asks), len(self._prev_bids), len(self._prev_asks), max_levels)

        for k in range(n_levels):
            weight = np.exp(-self.multi_level_decay * k)
            cur_b, prev_b = bids[k], self._prev_bids[k]
            cur_a, prev_a = asks[k], self._prev_asks[k]

            # Bid side contribution
            if cur_b.price > prev_b.price:
                delta_b = cur_b.size
            elif cur_b.price == prev_b.price:
                delta_b = cur_b.size - prev_b.size
            else:
                delta_b = -prev_b.size

            # Ask side contribution
            if cur_a.price > prev_a.price:
                delta_a = -prev_a.size
            elif cur_a.price == prev_a.price:
                delta_a = cur_a.size - prev_a.size
            else:
                delta_a = cur_a.size

            level_ofi = delta_b - delta_a
            total_ofi += weight * level_ofi

        self._prev_bids = bids[:max_levels]
        self._prev_asks = asks[:max_levels]
        return total_ofi

    @property
    def rolling_ofi(self) -> float:
        """Returns the cumulative rolling OFI over the sliding window."""
        if not self._ofi_history:
            return 0.0
        return float(np.sum(self._ofi_history))

    @property
    def ofi_zscore(self) -> float:
        if len(self._ofi_history) < 5:
            return 0.0
        arr = np.asarray(self._ofi_history, dtype=np.float64)
        std = np.std(arr)
        if std < 1e-12:
            return 0.0
        return float((arr[-1] - np.mean(arr)) / std)

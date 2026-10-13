"""Bipower Variation Volatility Estimator (Barndorff-Nielsen & Shephard 2004).

Separates continuous diffusive volatility from discrete Poisson jump components.
"""

from __future__ import annotations

import math
from typing import List, Tuple
import numpy as np


class BipowerVariationEstimator:
    """Computes Realized Bipower Variation (BV) and Realized Volatility (RV).

    BV = (pi / 2) * sum_{i=2}^N |r_i| * |r_{i-1}|
    RV = sum_{i=1}^N r_i^2
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._prices: List[float] = []

    def update(self, price: float) -> None:
        if price > 0:
            self._prices.append(price)
            if len(self._prices) > self.window_size:
                self._prices.pop(0)

    @property
    def ready(self) -> bool:
        return len(self._prices) >= 5

    def compute(self) -> Tuple[float, float, float]:
        """Returns (bipower_vol, realized_vol, jump_fraction).

        All volatilities are normalized per-interval.
        """
        if len(self._prices) < 3:
            return 0.0, 0.0, 0.0

        prices = np.asarray(self._prices, dtype=np.float64)
        returns = np.diff(np.log(prices))

        if len(returns) < 2:
            return 0.0, 0.0, 0.0

        # Realized Variance
        rv = float(np.sum(returns**2))

        # Realized Bipower Variation
        abs_ret = np.abs(returns)
        bv = float((math.pi / 2.0) * np.sum(abs_ret[1:] * abs_ret[:-1]))

        # Jump component (RV - BV) clipped at 0
        jump_var = max(0.0, rv - bv)
        jump_ratio = jump_var / rv if rv > 1e-12 else 0.0

        bv_vol = math.sqrt(max(0.0, bv / len(returns)))
        rv_vol = math.sqrt(max(0.0, rv / len(returns)))

        return bv_vol, rv_vol, jump_ratio

"""Variance Ratio Estimator (Lo-MacKinlay 1988).

Tests for mean-reversion (VR < 1) vs trending/momentum dynamics (VR > 1) across horizons.
"""

from __future__ import annotations

from typing import List, Tuple
import numpy as np


class VarianceRatioEstimator:
    """Computes Lo-MacKinlay variance ratio over a sliding price history."""

    def __init__(self, window_size: int = 200, horizons: Tuple[int, ...] = (2, 5, 10)):
        self.window_size = window_size
        self.horizons = horizons
        self._prices: List[float] = []

    def update(self, price: float) -> None:
        if price > 0:
            self._prices.append(price)
            if len(self._prices) > self.window_size:
                self._prices.pop(0)

    @property
    def ready(self) -> bool:
        max_q = max(self.horizons)
        return len(self._prices) >= max_q * 4

    def compute(self, horizon: int = 5) -> float:
        """Computes VR(q) = Var(r^q) / (q * Var(r)).

        VR < 1 indicates mean-reversion.
        VR > 1 indicates trending/persistence.
        VR == 1 indicates geometric random walk.
        """
        if len(self._prices) < horizon * 3:
            return 1.0

        p = np.asarray(self._prices, dtype=np.float64)
        log_p = np.log(p)
        r1 = np.diff(log_p)

        n = len(r1)
        if n < horizon:
            return 1.0

        var1 = np.var(r1, ddof=1)
        if var1 < 1e-12:
            return 1.0

        # Horizon-q returns
        rq = log_p[horizon:] - log_p[:-horizon]
        var_q = np.var(rq, ddof=1)

        vr = float(var_q / (horizon * var1))
        # Clip to realistic market bounds
        return float(np.clip(vr, 0.1, 5.0))

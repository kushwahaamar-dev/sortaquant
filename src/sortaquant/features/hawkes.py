"""Hawkes Process Branching Ratio Estimator for High-Frequency Flow (Bacry et al. 2015).

Estimates trade arrival self-excitation intensity and branching ratio n = alpha / beta.
When n approaches 1, the market experiences cascading order book feedback.
"""

from __future__ import annotations

import math
from typing import List
import numpy as np
from scipy.optimize import minimize


class HawkesIntensityEstimator:
    """Estimates 1D Hawkes process parameters (mu, alpha, beta) and branching ratio n = alpha / beta."""

    def __init__(self, window_size: int = 200):
        self.window_size = window_size
        self._timestamps_s: List[float] = []
        self._cached_branching_ratio: float = 0.2
        self._updates_since_fit: int = 0

    def update(self, timestamp_ns: int) -> float:
        t_sec = timestamp_ns * 1e-9
        self._timestamps_s.append(t_sec)
        if len(self._timestamps_s) > self.window_size:
            self._timestamps_s.pop(0)

        self._updates_since_fit += 1
        # Re-fit every 25 events if enough history
        if self._updates_since_fit >= 25 and len(self._timestamps_s) >= 40:
            self._fit()
            self._updates_since_fit = 0

        return self.branching_ratio

    @property
    def branching_ratio(self) -> float:
        return float(np.clip(self._cached_branching_ratio, 0.01, 0.99))

    def _fit(self) -> None:
        """Maximum Likelihood Estimation of 1D Hawkes Process."""
        t = np.asarray(self._timestamps_s, dtype=np.float64)
        t = t - t[0]  # Normalize start to 0
        T = t[-1]
        n_events = len(t)

        if T <= 1e-6 or n_events < 20:
            return

        inter_arrivals = np.diff(t)
        mean_dt = float(np.mean(inter_arrivals))
        if mean_dt <= 1e-6:
            return

        # Target beta roughly corresponds to decay over a few inter-arrival steps
        beta_init = 1.0 / (2.0 * mean_dt)
        alpha_init = 0.3 * beta_init
        mu_init = max(0.1, (n_events / T) * 0.7)

        def neg_log_likelihood(params: np.ndarray) -> float:
            mu, alpha, beta = params
            if mu <= 1e-6 or alpha <= 1e-6 or beta <= 1e-6 or alpha >= beta:
                return 1e9

            # Log-likelihood of Hawkes process:
            # sum_{i=1}^n ln(lambda(t_i)) - integral_0^T lambda(t) dt
            # integral_0^T lambda(t) dt = mu * T + sum_{i=1}^n (alpha / beta) * (1 - exp(-beta * (T - t_i)))
            r = 0.0
            ll = 0.0

            for i in range(1, n_events):
                dt = t[i] - t[i - 1]
                r = math.exp(-beta * dt) * (r + 1.0)
                lam_i = mu + alpha * r
                if lam_i <= 1e-9:
                    return 1e9
                ll += math.log(lam_i)

            # Add t[0] intensity
            ll += math.log(mu)

            # Integral component
            decay_terms = np.sum(1.0 - np.exp(-beta * (T - t)))
            integral = mu * T + (alpha / beta) * decay_terms

            return -(ll - integral)

        init_params = np.array([mu_init, alpha_init, beta_init], dtype=np.float64)
        bounds = [(1e-4, 500.0), (1e-4, 500.0), (1e-3, 1000.0)]

        try:
            res = minimize(
                neg_log_likelihood,
                init_params,
                bounds=bounds,
                method="L-BFGS-B",
                options={"maxiter": 30},
            )
            if res.success:
                _, alpha_opt, beta_opt = res.x
                self._cached_branching_ratio = float(alpha_opt / beta_opt)
        except Exception:
            pass

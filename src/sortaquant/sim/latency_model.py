"""Lognormal Latency Modeling with 2x Stress Testing."""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


class LatencyModel:
    """Simulates network jitter, order entry acknowledgement delay, and cancel latencies."""

    def __init__(
        self,
        median_latency_ms: float = 5.0,
        sigma_jitter: float = 0.40,
        stress_2x: bool = False,
        seed: Optional[int] = 42,
    ):
        self.median_latency_ms = median_latency_ms
        self.sigma_jitter = sigma_jitter
        self.stress_2x = stress_2x
        self._rng = np.random.default_rng(seed)

        # Lognormal parameter: median = exp(mu) => mu = ln(median)
        self.mu = math.log(max(0.1, median_latency_ms))

    def sample_latency_ms(self) -> float:
        """Samples a realistic latency duration in milliseconds."""
        sample = float(self._rng.lognormal(mean=self.mu, sigma=self.sigma_jitter))
        if self.stress_2x:
            sample *= 2.0  # 2x Stress testing mode
        return max(0.5, sample)

    def sample_latency_ns(self) -> int:
        return int(self.sample_latency_ms() * 1_000_000)

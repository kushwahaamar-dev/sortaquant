"""Bayesian Online Changepoint Detection (BOCPD) (Adams & MacKay 2007).

Provides zero-latency changepoint override alarms to protect against violent regime shifts.
"""

from __future__ import annotations

import math
from typing import Optional
import numpy as np
from scipy.special import gammaln


class BOCPDetector:
    """Online changepoint detection under Gaussian conjugate model."""

    def __init__(
        self,
        hazard_rate: float = 100.0,
        mu0: float = 0.0,
        kappa0: float = 1.0,
        alpha0: float = 1.0,
        beta0: float = 1.0,
        alarm_threshold: float = 0.80,
    ):
        self.hazard_rate = hazard_rate  # Expected run-length between changepoints
        self.alarm_threshold = alarm_threshold

        # Hyperparameters
        self.mu0 = mu0
        self.kappa0 = kappa0
        self.alpha0 = alpha0
        self.beta0 = beta0

        # State vectors (over current run lengths r = 0, 1, ..., t)
        self.R = np.array([1.0], dtype=np.float64)  # P(r_0 = 0) = 1
        self.mu = np.array([mu0], dtype=np.float64)
        self.kappa = np.array([kappa0], dtype=np.float64)
        self.alpha = np.array([alpha0], dtype=np.float64)
        self.beta = np.array([beta0], dtype=np.float64)

        self._last_alarm: bool = False
        self._changepoint_prob: float = 0.0

    @property
    def changepoint_probability(self) -> float:
        return self._changepoint_prob

    @property
    def has_alarm(self) -> bool:
        return self._last_alarm

    def _pred_prob(self, x: float) -> np.ndarray:
        """Student-t predictive probability density for observation x given sufficient statistics."""
        df = 2.0 * self.alpha
        scale_sq = (self.beta * (self.kappa + 1.0)) / (self.alpha * self.kappa)
        scale = np.sqrt(np.maximum(scale_sq, 1e-12))

        # Student-t log-pdf
        diff = (x - self.mu) / scale
        log_c = gammaln((df + 1.0) / 2.0) - gammaln(df / 2.0) - 0.5 * np.log(df * math.pi) - np.log(scale)
        log_p = log_c - ((df + 1.0) / 2.0) * np.log(1.0 + (diff**2) / df)

        return np.exp(np.clip(log_p, -100.0, 50.0))

    def update(self, x: float) -> bool:
        """Updates run-length distribution with scalar observation x (e.g. z-score or return).

        Returns True if changepoint alarm is triggered.
        """
        # 1. Predictive probability for existing run lengths
        pi_x = self._pred_prob(x)

        # 2. Hazard rate H(r)
        H = 1.0 / self.hazard_rate

        # 3. Growth probabilities (r_t = r_{t-1} + 1)
        growth_probs = self.R * pi_x * (1.0 - H)

        # 4. Changepoint probability (r_t = 0) under prior predictive
        pi_prior = float(self._pred_prob(x)[0]) if len(self.R) > 0 else 1.0
        # Evaluate under prior hyperparameters
        scale_sq_0 = (self.beta0 * (self.kappa0 + 1.0)) / (self.alpha0 * self.kappa0)
        scale_0 = math.sqrt(max(scale_sq_0, 1e-12))
        df_0 = 2.0 * self.alpha0
        diff_0 = (x - self.mu0) / scale_0
        log_c_0 = (
            gammaln((df_0 + 1.0) / 2.0)
            - gammaln(df_0 / 2.0)
            - 0.5 * math.log(df_0 * math.pi)
            - math.log(scale_0)
        )
        log_p_0 = log_c_0 - ((df_0 + 1.0) / 2.0) * math.log(1.0 + (diff_0**2) / df_0)
        pi_prior = math.exp(np.clip(log_p_0, -100.0, 50.0))

        cp_prob = float(np.sum(self.R * H)) * pi_prior

        # 5. Form new run-length distribution
        new_R = np.empty(len(self.R) + 1, dtype=np.float64)
        new_R[0] = cp_prob
        new_R[1:] = growth_probs

        # Normalize
        evidence = np.sum(new_R)
        if evidence > 1e-12:
            new_R /= evidence
        else:
            new_R = np.zeros_like(new_R)
            new_R[0] = 1.0

        # Truncate run lengths with negligible probability to preserve microsecond execution
        keep = new_R > 1e-5
        if not np.any(keep):
            keep[0] = True
        new_R = new_R[keep]
        new_R /= np.sum(new_R)

        # 6. Update hyperparameters
        new_mu = np.empty(len(self.R) + 1, dtype=np.float64)
        new_kappa = np.empty(len(self.R) + 1, dtype=np.float64)
        new_alpha = np.empty(len(self.R) + 1, dtype=np.float64)
        new_beta = np.empty(len(self.R) + 1, dtype=np.float64)

        # r_t = 0 resets to prior
        new_mu[0] = self.mu0
        new_kappa[0] = self.kappa0
        new_alpha[0] = self.alpha0
        new_beta[0] = self.beta0

        # r_t = r_{t-1} + 1 updates posterior parameters
        new_kappa[1:] = self.kappa + 1.0
        new_mu[1:] = (self.kappa * self.mu + x) / new_kappa[1:]
        new_alpha[1:] = self.alpha + 0.5
        new_beta[1:] = self.beta + (self.kappa * (x - self.mu) ** 2) / (2.0 * new_kappa[1:])

        # Apply truncation mask
        self.R = new_R
        self.mu = new_mu[keep]
        self.kappa = new_kappa[keep]
        self.alpha = new_alpha[keep]
        self.beta = new_beta[keep]

        self._changepoint_prob = float(self.R[0])
        self._last_alarm = self._changepoint_prob >= self.alarm_threshold

        return self._last_alarm

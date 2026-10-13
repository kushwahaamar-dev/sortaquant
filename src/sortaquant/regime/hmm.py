"""Four-State Hidden Markov Model (HMM) with Baum-Welch and Log-Sum-Exp Forward Filter.

Regimes:
R1: Calm / Mean-Reverting
R2: Trending / Informed
R3: High-Vol / Stress
R4: Structural Break / Flash Crash
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.special import logsumexp

from sortaquant.core.types import RegimeState


class FourStateHMM:
    """4-State HMM with Gaussian emissions and numerically stabilized forward filtering."""

    def __init__(
        self,
        n_features: int = 7,
        initial_pi: Optional[np.ndarray] = None,
        transition_matrix: Optional[np.ndarray] = None,
        means: Optional[np.ndarray] = None,
        covariances: Optional[np.ndarray] = None,
    ):
        self.n_states = 4
        self.n_features = n_features

        # Prior distribution pi_0
        self.pi = np.array([0.70, 0.20, 0.08, 0.02], dtype=np.float64) if initial_pi is None else np.asarray(initial_pi)

        # Baseline Transition Matrix A[j, k] = P(State_t = k | State_{t-1} = j)
        # Highly persistent diagonal
        if transition_matrix is None:
            self.A_base = np.array(
                [
                    [0.985, 0.010, 0.004, 0.001],  # R1 -> R1, R2, R3, R4
                    [0.020, 0.965, 0.012, 0.003],  # R2 -> R1, R2, R3, R4
                    [0.015, 0.020, 0.950, 0.015],  # R3 -> R1, R2, R3, R4
                    [0.050, 0.020, 0.080, 0.850],  # R4 -> R1, R2, R3, R4
                ],
                dtype=np.float64,
            )
        else:
            self.A_base = np.asarray(transition_matrix, dtype=np.float64)

        # Regime Feature Means:
        # Features: [bipower_vol*100, spread_bps, imbalance, vpin, ofi_zscore, vr_minus_1, hawkes_n]
        if means is None:
            self.means = np.array(
                [
                    [0.05, 0.80, 0.00, 0.15, 0.00, -0.20, 0.15],  # R1: Low vol, tight spread, low VPIN, MR, low Hawkes
                    [0.10, 1.20, 0.35, 0.35, 1.50, 0.40, 0.45],   # R2: Medium vol, trend, directional OFI, VR > 1
                    [0.45, 3.50, 0.00, 0.65, 0.00, 0.10, 0.70],   # R3: High vol, wide spread, high VPIN, high Hawkes
                    [1.20, 9.00, -0.70, 0.90, -2.50, 0.80, 0.92],  # R4: Extreme vol, blown spread, max VPIN, collapse
                ],
                dtype=np.float64,
            )
        else:
            self.means = np.asarray(means, dtype=np.float64)

        # Diagonal variances for emissions
        if covariances is None:
            self.variances = np.array(
                [
                    [0.01, 0.10, 0.05, 0.01, 0.25, 0.05, 0.02],
                    [0.02, 0.25, 0.10, 0.03, 0.50, 0.10, 0.04],
                    [0.15, 1.00, 0.20, 0.05, 1.00, 0.15, 0.05],
                    [0.50, 4.00, 0.30, 0.02, 2.00, 0.25, 0.02],
                ],
                dtype=np.float64,
            )
        else:
            self.variances = np.asarray(covariances, dtype=np.float64)

    def compute_log_emission(self, x: np.ndarray, state: int) -> float:
        """Computes log N(x; mu_state, diag_sigma_state)."""
        mu = self.means[state]
        var = self.variances[state]
        diff = x - mu
        # -0.5 * sum((x_d - mu_d)^2 / var_d + ln(2*pi*var_d))
        log_prob = -0.5 * np.sum((diff**2) / var + np.log(2.0 * math.pi * var))
        return float(log_prob)

    def get_tilted_transition_matrix(self, z_vix: float = 0.0, event_risk: int = 0) -> np.ndarray:
        """Tilts the transition matrix A toward R3 and R4 when macro volatility / event risk is elevated."""
        if abs(z_vix) < 1e-6 and event_risk == 0:
            return self.A_base.copy()

        # Tilt factor
        tilt_r3 = math.exp(max(0.0, z_vix) * 0.35 + event_risk * 0.8)
        tilt_r4 = math.exp(max(0.0, z_vix) * 0.50 + event_risk * 1.5)

        A_tilted = self.A_base.copy()
        for j in range(self.n_states):
            A_tilted[j, 2] *= tilt_r3
            A_tilted[j, 3] *= tilt_r4
            # Renormalize row
            A_tilted[j, :] /= np.sum(A_tilted[j, :])

        return A_tilted

    def forward_filter_step(
        self,
        current_pi: np.ndarray,
        x: np.ndarray,
        z_vix: float = 0.0,
        event_risk: int = 0,
    ) -> np.ndarray:
        """Computes online log-sum-exp stabilized forward update.

        pi_k(t) proportional to f_k(x_t) * sum_j pi_j(t-1) * A_{jk}
        """
        A = self.get_tilted_transition_matrix(z_vix=z_vix, event_risk=event_risk)

        # log_prior_k = log(sum_j pi_j * A_jk)
        log_pi = np.log(np.maximum(current_pi, 1e-12))
        log_A = np.log(np.maximum(A, 1e-12))

        log_next_pi = np.zeros(self.n_states, dtype=np.float64)

        for k in range(self.n_states):
            # log(sum_j exp(log_pi_j + log_A_jk))
            log_trans = logsumexp(log_pi + log_A[:, k])
            log_emit = self.compute_log_emission(x, k)
            log_next_pi[k] = log_trans + log_emit

        # Normalize via logsumexp
        norm_const = logsumexp(log_next_pi)
        posterior = np.exp(log_next_pi - norm_const)

        # Floor at 1e-6 and renormalize to avoid zeros
        posterior = np.maximum(posterior, 1e-6)
        posterior /= np.sum(posterior)
        return posterior

    def fit_offline(self, observations: List[np.ndarray], max_iters: int = 15) -> None:
        """Baum-Welch (EM) fitting algorithm for offline regime calibration."""
        if len(observations) < 50:
            return

        X = np.asarray(observations, dtype=np.float64)
        T, D = X.shape

        for _ in range(max_iters):
            # E-step: Forward-Backward
            log_alpha = np.zeros((T, self.n_states), dtype=np.float64)
            log_emit = np.zeros((T, self.n_states), dtype=np.float64)

            for t in range(T):
                for k in range(self.n_states):
                    log_emit[t, k] = self.compute_log_emission(X[t], k)

            log_A = np.log(np.maximum(self.A_base, 1e-12))
            log_alpha[0] = np.log(np.maximum(self.pi, 1e-12)) + log_emit[0]
            log_alpha[0] -= logsumexp(log_alpha[0])

            for t in range(1, T):
                for k in range(self.n_states):
                    log_alpha[t, k] = logsumexp(log_alpha[t - 1] + log_A[:, k]) + log_emit[t, k]
                log_alpha[t] -= logsumexp(log_alpha[t])

            # Backward pass
            log_beta = np.zeros((T, self.n_states), dtype=np.float64)
            for t in range(T - 2, -1, -1):
                for j in range(self.n_states):
                    log_beta[t, j] = logsumexp(log_A[j, :] + log_emit[t + 1, :] + log_beta[t + 1, :])
                log_beta[t] -= logsumexp(log_beta[t])

            # Gamma (smoothed state probabilities)
            log_gamma = log_alpha + log_beta
            log_gamma -= logsumexp(log_gamma, axis=1, keepdims=True)
            gamma = np.exp(log_gamma)

            # M-step: Update means and variances
            for k in range(self.n_states):
                w_sum = np.sum(gamma[:, k])
                if w_sum > 1e-4:
                    self.means[k] = np.sum(gamma[:, k : k + 1] * X, axis=0) / w_sum
                    diff = X - self.means[k]
                    self.variances[k] = np.maximum(
                        np.sum(gamma[:, k : k + 1] * (diff**2), axis=0) / w_sum, 1e-4
                    )

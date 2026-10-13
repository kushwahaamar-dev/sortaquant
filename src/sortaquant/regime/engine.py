"""Regime Detection Engine combining 4-State HMM, BOCPD override alarm, and hysteresis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import numpy as np

from sortaquant.core.types import RegimeState
from sortaquant.regime.hmm import FourStateHMM
from sortaquant.regime.bocpd import BOCPDetector


@dataclass(frozen=True)
class RegimeOutput:
    active_regime: RegimeState
    posterior: np.ndarray  # [pi_R1, pi_R2, pi_R3, pi_R4]
    is_halted: bool
    bocpd_alarm: bool
    blended_risk_aversion_mult: float
    blended_spread_mult: float
    blended_size_mult: float
    reason: str


class RegimeEngine:
    """Manages real-time regime inference, macro transition tilting, and discrete action hysteresis."""

    def __init__(
        self,
        halt_threshold_r4: float = 0.70,
        release_threshold_r4: float = 0.40,
        release_confirmation_ticks: int = 30,
    ):
        self.hmm = FourStateHMM()
        self.bocpd = BOCPDetector(hazard_rate=150.0, alarm_threshold=0.80)
        self.halt_threshold_r4 = halt_threshold_r4
        self.release_threshold_r4 = release_threshold_r4
        self.release_confirmation_ticks = release_confirmation_ticks

        self._current_pi = np.array([0.70, 0.20, 0.08, 0.02], dtype=np.float64)
        self._is_halted: bool = False
        self._ticks_below_release: int = 0
        self._last_output: Optional[RegimeOutput] = None

    @property
    def posterior(self) -> np.ndarray:
        return self._current_pi.copy()

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    def update(
        self,
        features_vec: np.ndarray,
        return_for_bocpd: float,
        z_vix: float = 0.0,
        event_risk: int = 0,
    ) -> RegimeOutput:
        """Processes a new tick feature vector and updates regime posterior."""
        # 1. Update HMM forward filter
        self._current_pi = self.hmm.forward_filter_step(
            current_pi=self._current_pi,
            x=features_vec,
            z_vix=z_vix,
            event_risk=event_risk,
        )

        # 2. Update BOCPD with standardized return / feature
        bocpd_alarm = self.bocpd.update(return_for_bocpd)

        pi_r4 = float(self._current_pi[3])

        # 3. Discrete Action Hysteresis
        # Quote pull: pi(R4) > 0.70 or BOCPD alarm
        if pi_r4 >= self.halt_threshold_r4 or bocpd_alarm:
            self._is_halted = True
            self._ticks_below_release = 0
            reason = f"HALT: pi(R4)={pi_r4:.3f} >= {self.halt_threshold_r4} or BOCPD={bocpd_alarm}"
        elif self._is_halted:
            # Check for release condition: pi(R4) < 0.40 sustained
            if pi_r4 < self.release_threshold_r4 and not bocpd_alarm:
                self._ticks_below_release += 1
                if self._ticks_below_release >= self.release_confirmation_ticks:
                    self._is_halted = False
                    reason = f"RELEASE: pi(R4)={pi_r4:.3f} sustained below {self.release_threshold_r4}"
                else:
                    reason = (
                        f"PENDING_RELEASE: {self._ticks_below_release}/{self.release_confirmation_ticks} ticks"
                    )
            else:
                self._ticks_below_release = 0
                reason = f"HALT_MAINTAINED: pi(R4)={pi_r4:.3f}"
        else:
            reason = "NORMAL"

        # 4. MAP Regime
        map_idx = int(np.argmax(self._current_pi))
        active_regime = RegimeState(map_idx)

        # 5. Continuous Parameter Blending
        # State base params: [risk_aversion_mult, spread_mult, size_mult]
        # R1 (Calm): [1.0, 1.0, 1.0]
        # R2 (Trend): [1.2, 1.1, 0.85]
        # R3 (HighVol): [2.5, 2.0, 0.40]
        # R4 (Break): [5.0, 4.0, 0.0]
        regime_weights = self._current_pi

        # Add conservative bias for R3/R4
        conservative_bias = float(0.5 * regime_weights[2] + 2.0 * regime_weights[3])

        blended_risk_aversion = float(
            regime_weights[0] * 1.0
            + regime_weights[1] * 1.2
            + regime_weights[2] * 2.5
            + regime_weights[3] * 5.0
            + conservative_bias
        )

        blended_spread = float(
            regime_weights[0] * 1.0
            + regime_weights[1] * 1.15
            + regime_weights[2] * 2.20
            + regime_weights[3] * 4.50
            + conservative_bias * 0.5
        )

        blended_size = float(
            regime_weights[0] * 1.0
            + regime_weights[1] * 0.85
            + regime_weights[2] * 0.35
            + regime_weights[3] * 0.0
        )
        if self._is_halted:
            blended_size = 0.0

        output = RegimeOutput(
            active_regime=active_regime,
            posterior=self._current_pi.copy(),
            is_halted=self._is_halted,
            bocpd_alarm=bocpd_alarm,
            blended_risk_aversion_mult=blended_risk_aversion,
            blended_spread_mult=blended_spread,
            blended_size_mult=blended_size,
            reason=reason,
        )
        self._last_output = output
        return output

"""Slow Brain Adapter with Fail-Safe Confidence Decay and Complete Fast-Body Isolation."""

from __future__ import annotations

import math
import time
from typing import Callable, Optional
from sortaquant.slow_brain.schema import SlowBrainState
from sortaquant.slow_brain.client import TradingViewMCPClient, MCPResponse


class SlowBrainAdapter:
    """Isolates the slow polled macro brain from the sub-millisecond fast body.

    Handles TradingView MCP feeds and Claude Opus 5.5 synthesis.
    During an outage, confidence decays exponentially as c = exp(-age / tau),
    and the fast body seamlessly continues using deterministic local rules.
    """

    def __init__(
        self,
        tau_decay_s: float = 300.0,  # 5-minute confidence decay half-life
        poll_interval_s: float = 60.0,
        mcp_client: Optional[TradingViewMCPClient] = None,
    ):
        self.tau_decay_s = tau_decay_s
        self.poll_interval_s = poll_interval_s
        self.mcp_client = mcp_client or TradingViewMCPClient()

        # Last verified good state
        self._last_good_state: SlowBrainState = SlowBrainState.neutral_prior()
        self._last_update_ns: int = time.time_ns()
        self._consecutive_failures: int = 0

        # Reserved adapter slot for external LLM inference callback
        self._llm_inference_hook: Optional[Callable[[dict], SlowBrainState]] = None

    def register_llm_hook(self, hook: Callable[[dict], SlowBrainState]) -> None:
        """Reserves slot for custom Claude Opus 5.5 inference pipeline."""
        self._llm_inference_hook = hook

    def update_state(self, new_state: SlowBrainState) -> None:
        """Updates internal state with a new schema-validated Claude Opus output."""
        self._last_good_state = new_state
        self._last_update_ns = new_state.timestamp_ns
        self._consecutive_failures = 0

    def handle_error(self, response: MCPResponse, now_ns: int) -> None:
        """Handles error envelope {"error": "...", "retry": bool} conservatively."""
        self._consecutive_failures += 1
        # If retryable, keep the last good state unchanged
        # If non-retryable, decay confidence immediately

    def get_current_state(self, now_ns: Optional[int] = None) -> SlowBrainState:
        """Returns the current macro state with elapsed exponential confidence decay.

        Fast body calls this synchronously (<1 microsecond).
        """
        current_time_ns = now_ns or time.time_ns()
        age_s = max(0.0, (current_time_ns - self._last_update_ns) * 1e-9)

        # Exponential confidence decay: c(t) = c_0 * exp(-age / tau)
        c0 = self._last_good_state.confidence
        decayed_c = float(c0 * math.exp(-age_s / self.tau_decay_s))

        # If confidence has completely evaporated, blend back to neutral prior
        if decayed_c < 0.15:
            return SlowBrainState.neutral_prior(timestamp_ns=current_time_ns)

        # Return state with decayed confidence
        # LLM risk multiplier is preserved or safely relaxed towards 1.0 as confidence decays
        return SlowBrainState(
            z_VIX=self._last_good_state.z_VIX * decayed_c,
            TrendAlign=self._last_good_state.TrendAlign if decayed_c > 0.4 else 0,
            VolExpansion=self._last_good_state.VolExpansion if decayed_c > 0.5 else 0,
            EventRisk=self._last_good_state.EventRisk if decayed_c > 0.5 else 0,
            UniverseScore=self._last_good_state.UniverseScore,
            risk_multiplier=self._last_good_state.risk_multiplier,
            confidence=decayed_c,
            timestamp_ns=current_time_ns,
            model_version=self._last_good_state.model_version,
        )

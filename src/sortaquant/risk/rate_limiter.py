"""Token Bucket Rate Limiter with Reject-Rate Exponential Backoff."""

from __future__ import annotations

import time


class TokenBucketRateLimiter:
    """Controls outgoing order and cancel message rates to conform to exchange limits."""

    def __init__(self, max_tokens: float = 20.0, refill_rate_per_sec: float = 20.0):
        self.capacity = max_tokens
        self.refill_rate = refill_rate_per_sec
        self.tokens = max_tokens
        self.last_refill_ns = 0
        self.backoff_factor = 1.0
        self.consecutive_rejects = 0

    def refill(self, now_ns: int) -> None:
        if self.last_refill_ns == 0:
            self.last_refill_ns = now_ns
            return

        elapsed_s = (now_ns - self.last_refill_ns) * 1e-9
        if elapsed_s > 0:
            effective_rate = self.refill_rate / self.backoff_factor
            self.tokens = min(self.capacity, self.tokens + elapsed_s * effective_rate)
            self.last_refill_ns = now_ns

    def can_send(self, now_ns: int, cost: float = 1.0) -> bool:
        self.refill(now_ns)
        return self.tokens >= cost

    def consume(self, now_ns: int, cost: float = 1.0) -> bool:
        self.refill(now_ns)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def on_reject(self) -> None:
        """Called upon receiving exchange 429 rate limit or order reject."""
        self.consecutive_rejects += 1
        self.backoff_factor = min(16.0, 2.0**self.consecutive_rejects)
        self.tokens = 0.0  # Drain bucket immediately

    def on_success(self) -> None:
        if self.consecutive_rejects > 0:
            self.consecutive_rejects = 0
            self.backoff_factor = 1.0

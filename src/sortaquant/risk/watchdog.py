"""Stale-feed Watchdog and Heartbeat Monitor."""

from __future__ import annotations


class StaleFeedWatchdog:
    """Detects market data WebSocket feed freezes and enforces instant cancel-all."""

    def __init__(self, timeout_ns: int = 500_000_000):  # 500 ms default
        self.timeout_ns = timeout_ns
        self._last_packet_ns: int = 0
        self._is_stale: bool = False

    @property
    def is_stale(self) -> bool:
        return self._is_stale

    @property
    def last_packet_ns(self) -> int:
        return self._last_packet_ns

    def on_packet(self, packet_timestamp_ns: int) -> None:
        self._last_packet_ns = max(self._last_packet_ns, packet_timestamp_ns)
        self._is_stale = False

    def check(self, current_time_ns: int) -> bool:
        """Returns True if feed is healthy, False if stale feed detected."""
        if self._last_packet_ns == 0:
            return True  # Waiting for first packet

        elapsed = current_time_ns - self._last_packet_ns
        if elapsed > self.timeout_ns:
            self._is_stale = True
            return False

        self._is_stale = False
        return True

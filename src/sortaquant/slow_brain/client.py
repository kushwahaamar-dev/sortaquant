"""TradingView MCP Client and Polling Throttler.

Official PyPI package: tradingview-mcp-server (`uvx --from tradingview-mcp-server tradingview-mcp`).
Constraints: Polled analysis layer only (60s cache, TA throttle max 4 concurrent and >=0.8s spacing).
Never sits in the tick loop. Handles error envelope {"error": "...", "retry": bool}.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MCPResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    retryable: bool = False
    cached: bool = False
    timestamp_ns: int = 0


class TradingViewMCPClient:
    """Polled, rate-limited, and cached client for TradingView MCP."""

    def __init__(
        self,
        cache_ttl_s: float = 60.0,
        min_call_spacing_s: float = 0.8,
        max_concurrent_calls: int = 4,
    ):
        self.cache_ttl_s = cache_ttl_s
        self.min_call_spacing_s = min_call_spacing_s
        self.max_concurrent_calls = max_concurrent_calls

        self._semaphore = asyncio.Semaphore(max_concurrent_calls)
        self._last_call_time_s: float = 0.0
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}  # key -> (expiry_time, data)
        self._lock = asyncio.Lock()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> MCPResponse:
        """Invokes a TradingView MCP tool under strict rate-limiting and caching."""
        cache_key = f"{tool_name}:{str(sorted(arguments.items()))}"
        now_s = time.time()
        now_ns = int(now_s * 1e9)

        # 1. Check Cache (60s TTL)
        async with self._lock:
            if cache_key in self._cache:
                expiry, cached_data = self._cache[cache_key]
                if now_s < expiry:
                    return MCPResponse(
                        success=True,
                        data=cached_data,
                        cached=True,
                        timestamp_ns=now_ns,
                    )

        # 2. Acquire concurrency semaphore and enforce spacing
        async with self._semaphore:
            async with self._lock:
                elapsed = now_s - self._last_call_time_s
                if elapsed < self.min_call_spacing_s:
                    await asyncio.sleep(self.min_call_spacing_s - elapsed)
                self._last_call_time_s = time.time()

            # 3. Simulate or dispatch MCP execution
            try:
                # Dispatch tool call (in real deployment, via stdio/mcp json-rpc)
                res_data = await self._execute_mcp_call(tool_name, arguments)

                # Store in cache
                async with self._lock:
                    self._cache[cache_key] = (time.time() + self.cache_ttl_s, res_data)

                return MCPResponse(
                    success=True,
                    data=res_data,
                    cached=False,
                    timestamp_ns=int(time.time() * 1e9),
                )
            except Exception as e:
                # Check for standard MCP error envelope: {"error": "...", "retry": bool}
                error_msg = str(e)
                retryable = "retryable" in error_msg.lower() or "timeout" in error_msg.lower()
                return MCPResponse(
                    success=False,
                    error_code=error_msg,
                    retryable=retryable,
                    timestamp_ns=int(time.time() * 1e9),
                )

    async def _execute_mcp_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Low-level tool invocation stub / mock."""
        # Provides mock synthetic data for testing / simulation
        await asyncio.sleep(0.05)
        return {
            "tool": tool_name,
            "symbol": arguments.get("symbol", "BINANCE:BTCUSDT"),
            "indicators": {
                "RSI": 52.0,
                "MACD": 12.5,
                "ATR": 150.0,
                "VIX": 18.2,
                "trend": "NEUTRAL",
            },
        }

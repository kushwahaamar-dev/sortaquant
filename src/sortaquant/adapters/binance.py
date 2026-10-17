"""Binance WebSocket feed adapter for depth and trade streams."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import websockets

from sortaquant.adapters.base import BaseExchangeAdapter
from sortaquant.core.types import BBO, BookLevel, Side, Trade


DepthHandler = Callable[[BBO, List[BookLevel], List[BookLevel]], None]


class BinancePayloadDecoder:
    """Decodes raw Binance WebSocket JSON payloads into domain events."""

    @staticmethod
    def unwrap(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Strip combined-stream envelope {"stream", "data"} when present."""
        if "data" in payload and isinstance(payload["data"], dict):
            return payload["data"]
        return payload

    @staticmethod
    def decode_depth20(
        payload: Dict[str, Any],
        symbol: str,
        timestamp_ns: int,
    ) -> Optional[Tuple[BBO, List[BookLevel], List[BookLevel]]]:
        """Decode depth20 / partial book. Accepts bids/asks or b/a keys."""
        bids_raw = payload.get("bids", payload.get("b", []))
        asks_raw = payload.get("asks", payload.get("a", []))
        if not bids_raw or not asks_raw:
            return None

        bids = [BookLevel(price=float(p), size=float(q)) for p, q in bids_raw]
        asks = [BookLevel(price=float(p), size=float(q)) for p, q in asks_raw]
        bbo = BBO(
            bid_price=bids[0].price,
            bid_size=bids[0].size,
            ask_price=asks[0].price,
            ask_size=asks[0].size,
            timestamp_ns=timestamp_ns,
        )
        return bbo, bids, asks

    @staticmethod
    def decode_trade(payload: Dict[str, Any]) -> Optional[Trade]:
        """Decode <symbol>@trade payload."""
        if payload.get("e") != "trade":
            return None

        price = float(payload["p"])
        size = float(payload["q"])
        t_ms = int(payload.get("T", payload.get("E", 0)))
        t_ns = t_ms * 1_000_000

        is_buyer_maker = bool(payload.get("m", False))
        side = Side.SELL if is_buyer_maker else Side.BUY

        return Trade(
            price=price,
            size=size,
            side=side,
            timestamp_ns=t_ns,
            trade_id=str(payload.get("t", "")),
            is_buyer_maker=is_buyer_maker,
        )


class BinanceWebSocketAdapter(BaseExchangeAdapter):
    """Live Binance market-data WebSocket. No order entry."""

    def __init__(
        self,
        symbol: str = "btcusdt",
        is_perp: bool = False,
        data_host: Optional[str] = None,
    ):
        super().__init__(symbol.upper())
        self.stream_symbol = symbol.lower().replace("/", "").replace("-", "")
        self.is_perp = is_perp
        # data-stream.binance.vision is the public market-data mirror (avoids geo 451
        # on stream.binance.com from some regions). Perps still use fstream.
        if data_host:
            host = data_host
        elif is_perp:
            host = "fstream.binance.com"
        else:
            host = "data-stream.binance.vision"
        streams = f"{self.stream_symbol}@depth20@100ms/{self.stream_symbol}@trade"
        self.ws_endpoint = f"wss://{host}/stream?streams={streams}"
        self._running: bool = False
        self.on_depth: Optional[DepthHandler] = None
        self._ws: Any = None

    async def connect(self) -> None:
        """Mark ready. Call run() to open the socket and pump messages."""
        self._running = True

    async def disconnect(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    def handle_raw_message(self, raw_json: str, local_timestamp_ns: int) -> None:
        try:
            data = json.loads(raw_json)
        except Exception:
            return

        data = BinancePayloadDecoder.unwrap(data)

        if "bids" in data or "b" in data:
            decoded = BinancePayloadDecoder.decode_depth20(data, self.symbol, local_timestamp_ns)
            if decoded is None:
                return
            bbo, bids, asks = decoded
            if self.on_depth:
                self.on_depth(bbo, bids, asks)
            elif self.on_bbo:
                self.on_bbo(bbo)
        elif data.get("e") == "trade":
            trade = BinancePayloadDecoder.decode_trade(data)
            if trade and self.on_trade:
                self.on_trade(trade)

    async def run(self, duration_s: Optional[float] = None) -> int:
        """Connect and pump messages until duration elapses or disconnect().

        Returns the number of raw frames handled.
        """
        self._running = True
        frames = 0
        deadline = (time.monotonic() + duration_s) if duration_s is not None else None

        async with websockets.connect(self.ws_endpoint, ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            while self._running:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                timeout = 1.0
                if deadline is not None:
                    timeout = max(0.05, min(1.0, deadline - time.monotonic()))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                self.handle_raw_message(raw if isinstance(raw, str) else raw.decode("utf-8"), time.time_ns())
                frames += 1

        self._ws = None
        self._running = False
        return frames

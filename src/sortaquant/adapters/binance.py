"""Binance WebSocket Feed Adapter for Depth and Trade Streams."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Tuple
from sortaquant.core.types import BBO, Side, Trade
from sortaquant.adapters.base import BaseExchangeAdapter


class BinancePayloadDecoder:
    """Decodes raw Binance WebSocket JSON payloads into strongly typed domain events."""

    @staticmethod
    def decode_depth20(payload: Dict[str, Any], symbol: str, timestamp_ns: int) -> Optional[BBO]:
        """Decodes `<symbol>@depth20` or snapshot payload."""
        bids = payload.get("bids", [])
        asks = payload.get("asks", [])
        if not bids or not asks:
            return None

        best_bid_p = float(bids[0][0])
        best_bid_q = float(bids[0][1])
        best_ask_p = float(asks[0][0])
        best_ask_q = float(asks[0][1])

        return BBO(
            bid_price=best_bid_p,
            bid_size=best_bid_q,
            ask_price=best_ask_p,
            ask_size=best_ask_q,
            timestamp_ns=timestamp_ns,
        )

    @staticmethod
    def decode_trade(payload: Dict[str, Any]) -> Optional[Trade]:
        """Decodes `<symbol>@trade` payload."""
        # e: "trade", p: price, q: quantity, T: timestamp, m: is_buyer_maker
        if payload.get("e") != "trade":
            return None

        price = float(payload["p"])
        size = float(payload["q"])
        t_ms = int(payload.get("T", payload.get("E", 0)))
        t_ns = t_ms * 1_000_000

        # In Binance protocol:
        # m = True indicates buyer was maker -> aggressor was seller -> trade side is SELL
        # m = False indicates buyer was taker -> aggressor was buyer -> trade side is BUY
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
    """Asynchronous WebSocket client adapter for Binance."""

    def __init__(self, symbol: str = "btcusdt", is_perp: bool = False):
        super().__init__(symbol.upper())
        self.stream_symbol = symbol.lower()
        self.is_perp = is_perp
        self.ws_endpoint = (
            f"wss://fstream.binance.com/ws/{self.stream_symbol}@depth20@100ms/{self.stream_symbol}@trade"
            if is_perp
            else f"wss://stream.binance.com:9443/ws/{self.stream_symbol}@depth20@100ms/{self.stream_symbol}@trade"
        )
        self._running: bool = False

    async def connect(self) -> None:
        self._running = True

    async def disconnect(self) -> None:
        self._running = False

    def handle_raw_message(self, raw_json: str, local_timestamp_ns: int) -> None:
        try:
            data = json.loads(raw_json)
        except Exception:
            return

        if "bids" in data and "asks" in data:
            bbo = BinancePayloadDecoder.decode_depth20(data, self.symbol, local_timestamp_ns)
            if bbo and self.on_bbo:
                self.on_bbo(bbo)
        elif data.get("e") == "trade":
            trade = BinancePayloadDecoder.decode_trade(data)
            if trade and self.on_trade:
                self.on_trade(trade)

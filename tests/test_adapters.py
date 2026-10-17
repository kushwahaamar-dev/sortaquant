"""Unit tests for exchange adapters and Binance payload decoder."""

import json

import pytest

from sortaquant.adapters.binance import BinancePayloadDecoder, BinanceWebSocketAdapter
from sortaquant.core.types import Side


def test_binance_depth_decoder():
    raw_depth = {
        "lastUpdateId": 987654321,
        "bids": [
            ["65000.50", "2.100"],
            ["65000.00", "5.400"],
        ],
        "asks": [
            ["65001.00", "1.800"],
            ["65001.50", "3.200"],
        ],
    }

    decoded = BinancePayloadDecoder.decode_depth20(raw_depth, "BTCUSDT", timestamp_ns=1000)
    assert decoded is not None
    bbo, bids, asks = decoded
    assert bbo.bid_price == 65000.50
    assert bbo.bid_size == 2.100
    assert bbo.ask_price == 65001.00
    assert bbo.ask_size == 1.800
    assert len(bids) == 2
    assert len(asks) == 2


def test_binance_trade_decoder():
    raw_trade = {
        "e": "trade",
        "E": 1700000000100,
        "s": "BTCUSDT",
        "t": 1234567,
        "p": "65000.50",
        "q": "0.250",
        "b": 88,
        "a": 99,
        "T": 1700000000100,
        "m": True,
    }

    trade = BinancePayloadDecoder.decode_trade(raw_trade)
    assert trade is not None
    assert trade.price == 65000.50
    assert trade.size == 0.250
    assert trade.side == Side.SELL
    assert trade.is_buyer_maker is True
    assert trade.timestamp_ns == 1700000000100 * 1_000_000


def test_combined_stream_unwrap_and_handle():
    adapter = BinanceWebSocketAdapter(symbol="btcusdt")
    seen = {}

    def on_depth(bbo, bids, asks):
        seen["bbo"] = bbo
        seen["n_bids"] = len(bids)

    def on_trade(trade):
        seen["trade"] = trade

    adapter.on_depth = on_depth
    adapter.on_trade = on_trade

    depth_frame = json.dumps(
        {
            "stream": "btcusdt@depth20@100ms",
            "data": {
                "lastUpdateId": 1,
                "bids": [["100.0", "1.0"]],
                "asks": [["100.1", "2.0"]],
            },
        }
    )
    adapter.handle_raw_message(depth_frame, local_timestamp_ns=5_000)
    assert seen["bbo"].bid_price == 100.0
    assert seen["n_bids"] == 1

    trade_frame = json.dumps(
        {
            "stream": "btcusdt@trade",
            "data": {
                "e": "trade",
                "p": "100.1",
                "q": "0.5",
                "T": 1700000000,
                "t": 9,
                "m": False,
            },
        }
    )
    adapter.handle_raw_message(trade_frame, local_timestamp_ns=6_000)
    assert seen["trade"].side == Side.BUY
    assert seen["trade"].price == 100.1


def test_ws_endpoint_uses_combined_stream_query():
    spot = BinanceWebSocketAdapter(symbol="btcfdusd")
    assert "data-stream.binance.vision" in spot.ws_endpoint
    assert "stream?streams=" in spot.ws_endpoint
    assert "btcfdusd@depth20@100ms" in spot.ws_endpoint
    assert "btcfdusd@trade" in spot.ws_endpoint
    perp = BinanceWebSocketAdapter(symbol="btcusdt", is_perp=True)
    assert "fstream.binance.com" in perp.ws_endpoint
    custom = BinanceWebSocketAdapter(symbol="btcusdt", data_host="stream.binance.com:9443")
    assert custom.ws_endpoint.startswith("wss://stream.binance.com:9443/")

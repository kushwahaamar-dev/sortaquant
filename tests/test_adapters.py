"""Unit tests for exchange adapters and Binance payload decoder."""

import json
import pytest

from sortaquant.adapters.binance import BinancePayloadDecoder
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

    bbo = BinancePayloadDecoder.decode_depth20(raw_depth, "BTCUSDT", timestamp_ns=1000)
    assert bbo is not None
    assert bbo.bid_price == 65000.50
    assert bbo.bid_size == 2.100
    assert bbo.ask_price == 65001.00
    assert bbo.ask_size == 1.800


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
        "m": True,  # buyer is maker => aggressor was seller => Side.SELL
    }

    trade = BinancePayloadDecoder.decode_trade(raw_trade)
    assert trade is not None
    assert trade.price == 65000.50
    assert trade.size == 0.250
    assert trade.side == Side.SELL
    assert trade.is_buyer_maker is True
    assert trade.timestamp_ns == 1700000000100 * 1_000_000

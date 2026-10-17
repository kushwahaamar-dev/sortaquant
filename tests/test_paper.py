"""Paper session wiring tests (no live network)."""

import json

import pytest

from sortaquant.runtime.paper import PaperSession, SessionPhase


def test_paper_session_handles_depth_and_trade_frames():
    session = PaperSession(symbol="BTCFDUSD")
    assert session.phase == SessionPhase.IDLE

    session.phase = SessionPhase.CONNECTING
    depth = json.dumps(
        {
            "stream": "btcfdusd@depth20@100ms",
            "data": {
                "lastUpdateId": 1,
                "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
                "asks": [["50001.00", "1.2"], ["50002.00", "3.0"]],
            },
        }
    )
    session._adapter.handle_raw_message(depth, local_timestamp_ns=1_000_000_000)
    assert session.bbo_count == 1
    assert session.phase == SessionPhase.LIVE
    assert session.simulator._last_mid == pytest.approx(50000.5)

    trade = json.dumps(
        {
            "stream": "btcfdusd@trade",
            "data": {
                "e": "trade",
                "p": "50000.00",
                "q": "2.0",
                "T": 1001,
                "t": 1,
                "m": True,
            },
        }
    )
    session._adapter.handle_raw_message(trade, local_timestamp_ns=1_010_000_000)
    assert session.trade_count == 1


def test_cli_simulate_runs():
    from sortaquant.cli import main

    assert main(["simulate", "--ticks", "50", "--seed", "1"]) == 0


def test_cli_audit_runs():
    from sortaquant.cli import main

    assert main(["audit"]) == 0

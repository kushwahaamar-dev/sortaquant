"""Unit tests for Slow Brain schema, TradingView MCP client, and fail-safe adapter."""

import asyncio
import pytest

from sortaquant.slow_brain.schema import SlowBrainState
from sortaquant.slow_brain.client import TradingViewMCPClient
from sortaquant.slow_brain.adapter import SlowBrainAdapter


def test_slow_brain_state_schema_validation():
    # Valid state
    state = SlowBrainState(
        z_VIX=1.5,
        TrendAlign=1,
        VolExpansion=1,
        EventRisk=0,
        UniverseScore=0.75,
        risk_multiplier=0.6,
        confidence=0.85,
    )
    assert state.z_VIX == 1.5
    assert state.risk_multiplier == 0.6
    assert state.model_version == "claude-opus-5-5"

    # Risk multiplier clamped if user/LLM attempts > 1.0
    state_clamp = SlowBrainState(risk_multiplier=1.8)
    assert state_clamp.risk_multiplier == 1.0


@pytest.mark.asyncio
async def test_tradingview_mcp_client_caching_and_spacing():
    client = TradingViewMCPClient(cache_ttl_s=2.0, min_call_spacing_s=0.2)

    # First call -> executes
    res1 = await client.call_tool("ta_summary", {"symbol": "BINANCE:BTCUSDT"})
    assert res1.success is True
    assert res1.cached is False

    # Second call within 2s -> returns CACHED response instantly!
    res2 = await client.call_tool("ta_summary", {"symbol": "BINANCE:BTCUSDT"})
    assert res2.success is True
    assert res2.cached is True


def test_slow_brain_adapter_confidence_decay_and_outage():
    adapter = SlowBrainAdapter(tau_decay_s=100.0)
    t0_ns = 1_000_000_000_000  # 1000s

    good_state = SlowBrainState(
        z_VIX=2.0,
        TrendAlign=1,
        VolExpansion=1,
        EventRisk=1,
        UniverseScore=0.8,
        risk_multiplier=0.5,
        confidence=1.0,
        timestamp_ns=t0_ns,
    )
    adapter.update_state(good_state)

    # Immediate access at t0
    s0 = adapter.get_current_state(now_ns=t0_ns)
    assert s0.confidence == pytest.approx(1.0)
    assert s0.risk_multiplier == 0.5
    assert s0.z_VIX == pytest.approx(2.0)

    # Check at 1 half-life (100 seconds later = 100 * 1e9 ns)
    t1_ns = t0_ns + 100_000_000_000
    s1 = adapter.get_current_state(now_ns=t1_ns)
    # c = 1.0 * exp(-1.0) ~ 0.3678
    assert s1.confidence == pytest.approx(0.3678, abs=0.01)

    # Check after severe outage: 10 half-lives (1000 seconds later)
    # Confidence decays below 0.15 -> must smoothly revert to neutral prior!
    t_outage_ns = t0_ns + 1_000_000_000_000
    s_outage = adapter.get_current_state(now_ns=t_outage_ns)
    assert s_outage.confidence == 0.0
    assert s_outage.z_VIX == 0.0
    assert s_outage.TrendAlign == 0
    assert s_outage.EventRisk == 0
    assert s_outage.model_version == "neutral-prior"

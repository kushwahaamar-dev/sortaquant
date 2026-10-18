"""Operator readiness checks for sortaquant."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import asdict, dataclass
from typing import List


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class DoctorReport:
    ok: bool
    checks: List[DoctorCheck]
    version: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "version": self.version,
            "checks": [asdict(c) for c in self.checks],
        }


def _check_imports() -> DoctorCheck:
    missing = []
    for mod in ("numpy", "scipy", "pydantic", "websockets"):
        try:
            importlib.import_module(mod)
        except Exception as exc:
            missing.append(f"{mod}: {exc}")
    if missing:
        return DoctorCheck("imports", False, "; ".join(missing))
    return DoctorCheck("imports", True, "numpy scipy pydantic websockets")


def _check_gate1() -> DoctorCheck:
    from sortaquant.validation.gate1 import Gate1HurdleValidator

    audit = Gate1HurdleValidator.audit_venues()
    passed = [k for k, r in audit.items() if r.passed]
    if not passed:
        return DoctorCheck("gate1", False, "no venues pass Gate 1")
    return DoctorCheck("gate1", True, f"pass={','.join(passed)}")


async def _probe_feed(symbol: str = "btcusdt", timeout_s: float = 8.0) -> DoctorCheck:
    import websockets

    from sortaquant.adapters.binance import BinanceWebSocketAdapter

    adapter = BinanceWebSocketAdapter(symbol=symbol)
    try:
        async with websockets.connect(adapter.ws_endpoint, open_timeout=5, ping_interval=None) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
            text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            if not text:
                return DoctorCheck("feed", False, f"empty frame from {adapter.ws_endpoint}")
            return DoctorCheck("feed", True, f"frame from {adapter.ws_endpoint}")
    except Exception as exc:
        return DoctorCheck("feed", False, f"{type(exc).__name__}: {exc}")


def _check_simulate_smoke() -> DoctorCheck:
    from sortaquant.adapters.replay import MarketReplayAdapter
    from sortaquant.sim.simulator import TickSimulator

    bbos, trades = MarketReplayAdapter.generate_synthetic_regime_dataset(n_ticks=80, seed=7)
    sim = TickSimulator(symbol="BTCFDUSD", maker_fee_bps=0.0)
    sim.run_tape(bbos, trades)
    summary = sim.summarize()
    return DoctorCheck(
        "simulate_smoke",
        True,
        f"fills={summary.total_trades} net_pnl={summary.total_net_pnl:.4f}",
    )


def run_doctor(probe_feed: bool = True) -> DoctorReport:
    """Run readiness checks. Returns ok=True only if every check passes."""
    from sortaquant import __version__

    checks: List[DoctorCheck] = [
        _check_imports(),
        _check_gate1(),
        _check_simulate_smoke(),
    ]
    if probe_feed:
        checks.append(asyncio.run(_probe_feed()))
    ok = all(c.ok for c in checks)
    return DoctorReport(ok=ok, checks=checks, version=__version__)

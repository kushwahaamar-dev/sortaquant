#!/usr/bin/env python3
"""Drive sortaquant the way an operator does, and record proof JSON.

Run from the sortaquant repo root:

    uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py doctor
    uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py simulate --run-id <id>
    uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py gate1 --run-id <id>
    uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py stale-feed --run-id <id>
    uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py inventory-clamp --run-id <id>
    uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py cleanup --run-id <id>
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and 'name = "sortaquant"' in pyproject.read_text():
            return candidate
    sys.stderr.write("sqv: run this from the sortaquant repo (pyproject name sortaquant)\n")
    sys.exit(2)


def artifact_dir(root: Path, run_id: str, feature: str) -> Path:
    path = root / ".cursor" / "skills" / "verify-sortaquant" / "artifacts" / run_id / feature
    path.mkdir(parents=True, exist_ok=True)
    return path


def scratch_dir(run_id: str) -> Path:
    path = Path(f"/tmp/sortaquant-verify-{run_id}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def mark_started(run_id: str) -> None:
    pid_path = scratch_dir(run_id) / "pid"
    pid_path.write_text(str(os.getpid()))


def mark_finished(run_id: str) -> None:
    pid_path = scratch_dir(run_id) / "pid"
    if pid_path.is_file() and pid_path.read_text().strip() == str(os.getpid()):
        pid_path.unlink()


def write_result(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


def fail(path: Path, payload: dict) -> None:
    payload["ok"] = False
    write_result(path, payload)
    sys.exit(1)


def cmd_doctor(_args: argparse.Namespace) -> None:
    root = repo_root()
    import importlib.metadata

    import sortaquant
    from sortaquant.adapters.replay import MarketReplayAdapter
    from sortaquant.risk.governor import DeterministicRiskGovernor
    from sortaquant.sim.simulator import TickSimulator
    from sortaquant.validation.gate1 import Gate1HurdleValidator

    version = importlib.metadata.version("sortaquant")
    hello = sortaquant.hello()
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    problems = []
    if version != "0.1.0":
        problems.append(f"version {version} != 0.1.0")
    if hello != "Hello from sortaquant!":
        problems.append(f"hello() returned {hello!r}")
    if sys.version_info < (3, 11):
        problems.append(f"python {py} is below 3.11")
    for label, obj in (
        ("TickSimulator", TickSimulator),
        ("MarketReplayAdapter", MarketReplayAdapter),
        ("Gate1HurdleValidator", Gate1HurdleValidator),
        ("DeterministicRiskGovernor", DeterministicRiskGovernor),
    ):
        if obj is None:
            problems.append(f"missing {label}")

    payload = {
        "ok": not problems,
        "package": "sortaquant",
        "version": version,
        "python": py,
        "hello": hello,
        "repo": str(root),
        "network": "not contacted",
        "problems": problems,
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    sys.exit(0 if payload["ok"] else 1)


def cmd_simulate(args: argparse.Namespace) -> None:
    root = repo_root()
    mark_started(args.run_id)
    out = artifact_dir(root, args.run_id, "simulation")
    try:
        from sortaquant.adapters.replay import MarketReplayAdapter
        from sortaquant.core.types import RegimeState
        from sortaquant.sim.simulator import TickSimulator

        bbos, trades = MarketReplayAdapter.generate_synthetic_regime_dataset(
            n_ticks=args.ticks,
            seed=args.seed,
        )
        sim = TickSimulator(
            symbol=args.symbol,
            maker_fee_bps=args.maker_fee_bps,
            stress_latency_2x=args.stress_latency_2x,
        )
        regimes = set()
        halt_observed = False
        trade_idx = 0
        for bbo in bbos:
            while trade_idx < len(trades) and trades[trade_idx].timestamp_ns < bbo.timestamp_ns:
                sim.step_trade(trades[trade_idx])
                trade_idx += 1
            sim.step_bbo(bbo)
            posterior = sim.regime_engine.posterior
            active = RegimeState(max(range(len(posterior)), key=lambda i: posterior[i]))
            regimes.add(active.name)
            if sim.regime_engine.is_halted:
                halt_observed = True
        while trade_idx < len(trades):
            sim.step_trade(trades[trade_idx])
            trade_idx += 1
        summary = sim.summarize()
        payload = {
            "ok": True,
            "command": "simulate",
            "symbol": args.symbol,
            "n_ticks": args.ticks,
            "seed": args.seed,
            "maker_fee_bps": args.maker_fee_bps,
            "stress_latency_2x": args.stress_latency_2x,
            "bbo_count": len(bbos),
            "tape_trade_count": len(trades),
            "total_trades": summary.total_trades,
            "total_volume": summary.total_volume,
            "maker_fees_paid": summary.maker_fees_paid,
            "total_net_pnl": summary.total_net_pnl,
            "markout_1s_avg_bps": summary.markout_1s_avg_bps,
            "max_drawdown_pct": summary.max_drawdown_pct,
            "halt_observed": halt_observed,
            "regimes": sorted(regimes),
            "position": sim.risk_governor.current_position,
            "loop": "causal: trades before each bbo",
        }
        problems = []
        if payload["bbo_count"] != args.ticks:
            problems.append("bbo_count != n_ticks")
        if args.maker_fee_bps == 0.0 and payload["maker_fees_paid"] != 0.0:
            problems.append("maker fee was charged on a zero-fee run")
        canonical = (
            args.ticks == 500
            and args.seed == 42
            and args.symbol == "BTCFDUSD"
            and args.maker_fee_bps == 0.0
            and not args.stress_latency_2x
        )
        if canonical:
            if payload["tape_trade_count"] != 250:
                problems.append("canonical tape is 250 trades")
            if payload["total_trades"] != 1:
                problems.append("canonical fill count is 1")
            if not payload["halt_observed"]:
                problems.append("canonical replay did not halt")
            expected = {"R1_CALM", "R2_TRENDING", "R3_HIGHVOL", "R4_BREAK"}
            if set(payload["regimes"]) != expected:
                problems.append(f"regimes {payload['regimes']} != {sorted(expected)}")
        if problems:
            payload["problems"] = problems
            fail(out / "result.json", payload)
        write_result(out / "result.json", payload)
    finally:
        mark_finished(args.run_id)


def cmd_gate1(args: argparse.Namespace) -> None:
    root = repo_root()
    mark_started(args.run_id)
    out = artifact_dir(root, args.run_id, "gate1-audit")
    try:
        from sortaquant.validation.gate1 import Gate1HurdleValidator

        audit = Gate1HurdleValidator.audit_venues()
        venues = {
            key: {
                "passed": result.passed,
                "net_edge_bps": result.net_edge_bps,
                "instrument": result.instrument,
                "venue": result.venue,
                "message": result.message,
            }
            for key, result in audit.items()
        }
        payload = {
            "ok": True,
            "command": "gate1",
            "network": "not contacted",
            "venues": venues,
        }
        expect = {
            "binance_spot_btcusdt_vip0": False,
            "binance_spot_btcfdusd_zerofee": True,
            "binance_perp_btcusdt_vip0": False,
            "binance_perp_btcusdt_mm_rebate": True,
            "binance_perp_solusdt_vip1": True,
        }
        problems = []
        for key, passed in expect.items():
            if key not in venues:
                problems.append(f"missing {key}")
            elif venues[key]["passed"] is not passed:
                problems.append(f"{key} passed={venues[key]['passed']} expected {passed}")
        fdusd = venues.get("binance_spot_btcfdusd_zerofee", {})
        if fdusd and abs(fdusd.get("net_edge_bps", 0) - 0.35) > 1e-9:
            problems.append(f"BTC/FDUSD net edge {fdusd.get('net_edge_bps')} != 0.35")
        if problems:
            payload["problems"] = problems
            fail(out / "result.json", payload)
        write_result(out / "result.json", payload)
    finally:
        mark_finished(args.run_id)


def _sample_quotes(now_ns: int):
    from sortaquant.core.types import QuotePair, RegimeState

    return QuotePair(
        bid_price=59999.0,
        bid_size=0.1,
        ask_price=60001.0,
        ask_size=0.1,
        fair_value=60000.0,
        regime=RegimeState.R1_CALM,
        timestamp_ns=now_ns,
    )


def cmd_stale_feed(args: argparse.Namespace) -> None:
    root = repo_root()
    mark_started(args.run_id)
    out = artifact_dir(root, args.run_id, "stale-feed-halt")
    try:
        from sortaquant.risk.governor import DeterministicRiskGovernor

        governor = DeterministicRiskGovernor()
        t0 = 1_700_000_000_000_000_000
        governor.on_feed_packet(t0)
        fresh = governor.evaluate_quotes(
            quotes=_sample_quotes(t0),
            mid_price=60000.0,
            volatility=0.01,
            l2_depth=10.0,
            now_ns=t0 + 100_000_000,
        )
        stale = governor.evaluate_quotes(
            quotes=_sample_quotes(t0),
            mid_price=60000.0,
            volatility=0.01,
            l2_depth=10.0,
            now_ns=t0 + 600_000_000,
        )
        payload = {
            "ok": True,
            "command": "stale-feed",
            "fresh_halted": fresh.is_halted,
            "fresh_bid": fresh.bid_price,
            "fresh_ask": fresh.ask_price,
            "stale_halted": stale.is_halted,
            "stale_bid": stale.bid_price,
            "stale_ask": stale.ask_price,
            "stale_reasons": stale.reasons,
            "gap_ms": 600,
        }
        problems = []
        if fresh.is_halted or fresh.bid_price is None or fresh.ask_price is None:
            problems.append("100ms gap halted or pulled quotes")
        if not stale.is_halted or stale.bid_price is not None or stale.ask_price is not None:
            problems.append("600ms gap did not cancel both sides")
        if not any("Stale feed" in reason for reason in stale.reasons):
            problems.append(f"stale reasons {stale.reasons}")
        if problems:
            payload["problems"] = problems
            fail(out / "result.json", payload)
        write_result(out / "result.json", payload)
    finally:
        mark_finished(args.run_id)


def cmd_inventory_clamp(args: argparse.Namespace) -> None:
    root = repo_root()
    mark_started(args.run_id)
    out = artifact_dir(root, args.run_id, "inventory-clamp")
    try:
        from sortaquant.core.types import Fill, Side
        from sortaquant.risk.governor import DeterministicRiskGovernor

        governor = DeterministicRiskGovernor()
        t0 = 1_700_000_000_000_000_000
        governor.on_feed_packet(t0)
        fill = Fill(
            fill_id="verify-fill-1",
            order_id="verify-order-1",
            symbol="BTCFDUSD",
            side=Side.BUY,
            price=60000.0,
            size=0.5,
            fee=0.0,
            is_maker=True,
            timestamp_ns=t0,
            mid_at_fill=60000.0,
        )
        governor.on_fill(fill, current_mid=60000.0)
        governed = governor.evaluate_quotes(
            quotes=_sample_quotes(t0),
            mid_price=60000.0,
            volatility=0.01,
            l2_depth=10.0,
            now_ns=t0 + 100_000_000,
        )
        payload = {
            "ok": True,
            "command": "inventory-clamp",
            "position": governor.current_position,
            "q_max": governed.q_max,
            "bid": governed.bid_price,
            "ask": governed.ask_price,
            "reasons": governed.reasons,
            "halted": governed.is_halted,
        }
        problems = []
        if not (governor.current_position >= governed.q_max):
            problems.append("position did not reach Q_max")
        if governed.bid_price is not None or governed.bid_size != 0.0:
            problems.append("bid was not cancelled at the ceiling")
        if governed.ask_price is None:
            problems.append("ask was pulled along with the bid")
        if not any(reason.startswith("CLAMP:") for reason in governed.reasons):
            problems.append(f"no CLAMP reason in {governed.reasons}")
        if problems:
            payload["problems"] = problems
            fail(out / "result.json", payload)
        write_result(out / "result.json", payload)
    finally:
        mark_finished(args.run_id)


def _pid_is_ours(pid: int, run_id: str) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
        )
    except subprocess.CalledProcessError:
        return False
    return "sqv.py" in command and run_id in command


def cmd_cleanup(args: argparse.Namespace) -> None:
    scratch = Path(f"/tmp/sortaquant-verify-{args.run_id}")
    killed = None
    pid_path = scratch / "pid"
    if pid_path.is_file():
        raw = pid_path.read_text().strip()
        if raw.isdigit() and _pid_is_ours(int(raw), args.run_id):
            os.kill(int(raw), signal.SIGTERM)
            killed = int(raw)
        if pid_path.is_file():
            pid_path.unlink()
    if scratch.is_dir():
        for child in scratch.iterdir():
            if child.is_file():
                child.unlink()
        scratch.rmdir()
    payload = {
        "ok": True,
        "command": "cleanup",
        "run_id": args.run_id,
        "killed_pid": killed,
        "scratch_removed": not scratch.exists(),
        "artifacts_untouched": True,
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sqv")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    simulate = sub.add_parser("simulate")
    simulate.add_argument("--run-id", required=True)
    simulate.add_argument("--ticks", type=int, default=500)
    simulate.add_argument("--seed", type=int, default=42)
    simulate.add_argument("--symbol", default="BTCFDUSD")
    simulate.add_argument("--maker-fee-bps", type=float, default=0.0)
    simulate.add_argument("--stress-latency-2x", action="store_true")
    simulate.set_defaults(func=cmd_simulate)

    for name, func in (
        ("gate1", cmd_gate1),
        ("stale-feed", cmd_stale_feed),
        ("inventory-clamp", cmd_inventory_clamp),
        ("cleanup", cmd_cleanup),
    ):
        command = sub.add_parser(name)
        command.add_argument("--run-id", required=True)
        command.set_defaults(func=func)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

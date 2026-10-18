"""sortaquant operator CLI: simulate, paper, audit, doctor, walkforward."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _write_json(path: Optional[str], payload: Dict[str, Any]) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)


def _cmd_simulate(args: argparse.Namespace) -> int:
    from sortaquant.adapters.replay import MarketReplayAdapter
    from sortaquant.sim.simulator import TickSimulator

    bbos, trades = MarketReplayAdapter.generate_synthetic_regime_dataset(
        n_ticks=args.ticks,
        seed=args.seed,
    )
    sim = TickSimulator(symbol=args.symbol, maker_fee_bps=args.maker_fee_bps)
    sim.run_tape(bbos, trades)
    summary = sim.summarize()
    print(
        f"simulate {args.symbol}: fills={summary.total_trades} "
        f"net_pnl=${summary.total_net_pnl:.2f} "
        f"markout_1s={summary.markout_1s_avg_bps:.3f}bps "
        f"max_dd={summary.max_drawdown_pct*100:.3f}%"
    )
    _write_json(
        args.json_out,
        {
            "command": "simulate",
            "symbol": args.symbol,
            "ticks": args.ticks,
            "seed": args.seed,
            "total_trades": summary.total_trades,
            "total_net_pnl": summary.total_net_pnl,
            "markout_1s_avg_bps": summary.markout_1s_avg_bps,
            "max_drawdown_pct": summary.max_drawdown_pct,
            "sharpe_ratio": summary.sharpe_ratio,
        },
    )
    return 0


def _cmd_paper(args: argparse.Namespace) -> int:
    from sortaquant.runtime.paper import PaperSession, PaperSnapshot

    def _hb(snap: PaperSnapshot) -> None:
        bid = f"{snap.bid_price:.2f}" if snap.bid_price is not None else "-"
        ask = f"{snap.ask_price:.2f}" if snap.ask_price is not None else "-"
        print(
            f"[{snap.elapsed_s:5.1f}s] mid={snap.last_mid:.2f} "
            f"quote={bid}/{ask} fills={snap.fills} pos={snap.position:.6f} "
            f"pnl=${snap.net_pnl:.2f} bbo={snap.bbo_count} tape={snap.trade_count}",
            flush=True,
        )

    session = PaperSession(
        symbol=args.symbol,
        is_perp=args.perp,
        maker_fee_bps=args.maker_fee_bps,
        data_host=args.data_host,
        heartbeat_s=args.heartbeat,
        on_heartbeat=_hb if args.heartbeat > 0 else None,
    )
    print(
        f"paper {session.symbol} for {args.duration}s "
        f"(live Binance book, virtual fills, no orders)...",
        flush=True,
    )
    try:
        summary = session.run_blocking(duration_s=args.duration)
    except Exception as exc:
        print(f"paper failed: {exc}", file=sys.stderr)
        return 1
    snap = session.snapshot()
    print(
        f"done phase={snap.phase.value} bbos={snap.bbo_count} tape={snap.trade_count} "
        f"fills={summary.total_trades} pos={snap.position:.6f} "
        f"net_pnl=${summary.total_net_pnl:.2f} "
        f"markout_1s={summary.markout_1s_avg_bps:.3f}bps "
        f"last_mid={snap.last_mid:.2f}"
    )
    payload = snap.to_dict()
    payload.update(
        {
            "command": "paper",
            "total_net_pnl": summary.total_net_pnl,
            "maker_fees_paid": summary.maker_fees_paid,
            "sharpe_ratio": summary.sharpe_ratio,
            "max_drawdown_pct": summary.max_drawdown_pct,
        }
    )
    _write_json(args.json_out, payload)
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    from sortaquant.validation.gate1 import Gate1HurdleValidator

    audit = Gate1HurdleValidator.audit_venues()
    rows = []
    for key, res in audit.items():
        rows.append(
            {
                "key": key,
                "passed": res.passed,
                "net_edge_bps": res.net_edge_bps,
                "message": res.message,
            }
        )
        mark = "PASS" if res.passed else "FAIL"
        print(f"[{mark}] {res.message}")
    if args.json:
        print(json.dumps(rows, indent=2))
    _write_json(args.json_out, {"command": "audit", "venues": rows})
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from sortaquant.runtime.doctor import run_doctor

    report = run_doctor(probe_feed=not args.offline)
    for check in report.checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
    print(f"doctor version={report.version} ok={report.ok}")
    _write_json(args.json_out, report.to_dict())
    return 0 if report.ok else 1


def _cmd_walkforward(args: argparse.Namespace) -> int:
    from sortaquant.adapters.replay import MarketReplayAdapter
    from sortaquant.validation.walk_forward import WalkForwardValidator

    bbos, trades = MarketReplayAdapter.generate_synthetic_regime_dataset(
        n_ticks=args.ticks,
        seed=args.seed,
    )
    validator = WalkForwardValidator(
        n_splits=args.splits,
        purge_window_ticks=args.purge,
        embargo_window_ticks=args.embargo,
        maker_fee_bps=args.maker_fee_bps,
    )
    report = validator.run_walk_forward(bbos, trades)
    print(
        f"walkforward splits={report.n_splits} "
        f"gate1_pass_rate={report.gate1_pass_rate:.2f} "
        f"mean_pnl=${report.mean_test_net_pnl:.2f} "
        f"max_dd={report.max_test_drawdown_pct*100:.3f}%"
    )
    for split in report.splits:
        print(
            f"  split {split.split_index}: train={split.train_size} test={split.test_size} "
            f"fitted={split.fitted} embargo_gap={split.embargo_gap} "
            f"gate1={split.gate1_passed} pnl=${split.test_net_pnl:.2f}"
        )
    _write_json(
        args.json_out,
        {
            "command": "walkforward",
            "n_splits": report.n_splits,
            "mean_test_sharpe": report.mean_test_sharpe,
            "mean_test_net_pnl": report.mean_test_net_pnl,
            "max_test_drawdown_pct": report.max_test_drawdown_pct,
            "gate1_pass_rate": report.gate1_pass_rate,
            "splits": [split.__dict__ for split in report.splits],
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sortaquant",
        description="RAIM market-making operator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sim_p = sub.add_parser("simulate", help="Synthetic tape through TickSimulator")
    sim_p.add_argument("--symbol", default="BTCFDUSD")
    sim_p.add_argument("--ticks", type=int, default=500)
    sim_p.add_argument("--seed", type=int, default=42)
    sim_p.add_argument("--maker-fee-bps", type=float, default=0.0)
    sim_p.add_argument("--json-out", default=None, help="Write run summary JSON")
    sim_p.set_defaults(func=_cmd_simulate)

    paper_p = sub.add_parser("paper", help="Live Binance book, virtual fills (no orders)")
    paper_p.add_argument("--symbol", default="BTCUSDT", help="e.g. BTCUSDT or BTCFDUSD")
    paper_p.add_argument("--duration", type=float, default=30.0, help="Seconds to run")
    paper_p.add_argument("--perp", action="store_true", help="Use USDM futures stream")
    paper_p.add_argument("--maker-fee-bps", type=float, default=0.0)
    paper_p.add_argument(
        "--data-host",
        default=None,
        help="WebSocket host override (default: data-stream.binance.vision)",
    )
    paper_p.add_argument(
        "--heartbeat",
        type=float,
        default=5.0,
        help="Seconds between live status lines (0 disables)",
    )
    paper_p.add_argument("--json-out", default=None, help="Write run summary JSON")
    paper_p.set_defaults(func=_cmd_paper)

    audit_p = sub.add_parser("audit", help="Gate 1 venue feasibility table")
    audit_p.add_argument("--json", action="store_true")
    audit_p.add_argument("--json-out", default=None)
    audit_p.set_defaults(func=_cmd_audit)

    doctor_p = sub.add_parser("doctor", help="Readiness checks before opportunity hunting")
    doctor_p.add_argument(
        "--offline",
        action="store_true",
        help="Skip live market-data probe",
    )
    doctor_p.add_argument("--json-out", default=None)
    doctor_p.set_defaults(func=_cmd_doctor)

    wf_p = sub.add_parser("walkforward", help="Purged+embargoed walk-forward on synthetic tape")
    wf_p.add_argument("--ticks", type=int, default=500)
    wf_p.add_argument("--seed", type=int, default=42)
    wf_p.add_argument("--splits", type=int, default=3)
    wf_p.add_argument("--purge", type=int, default=20)
    wf_p.add_argument("--embargo", type=int, default=20)
    wf_p.add_argument("--maker-fee-bps", type=float, default=0.0)
    wf_p.add_argument("--json-out", default=None)
    wf_p.set_defaults(func=_cmd_walkforward)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""sortaquant operator CLI: simulate, paper, audit."""

from __future__ import annotations

import argparse
import json
import sys


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
    return 0


def _cmd_paper(args: argparse.Namespace) -> int:
    from sortaquant.runtime.paper import PaperSession

    session = PaperSession(
        symbol=args.symbol,
        is_perp=args.perp,
        maker_fee_bps=args.maker_fee_bps,
        data_host=args.data_host,
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
    paper_p.set_defaults(func=_cmd_paper)

    audit_p = sub.add_parser("audit", help="Gate 1 venue feasibility table")
    audit_p.add_argument("--json", action="store_true")
    audit_p.set_defaults(func=_cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

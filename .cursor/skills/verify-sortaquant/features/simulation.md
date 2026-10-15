# Tick simulation

Tick simulation replays a synthetic regime-switching book through the fast body and prints a performance summary. The operator path streams every BBO, then every trade.

## Sub-features

- `sim-replay` feeds 500 BBOs and the generated tape into `TickSimulator`.
- `sim-zero-fee` charges no maker fee on `BTCFDUSD` at 0 bps.
- `sim-regimes` visits calm, trending, high-vol, and break posteriors.
- `sim-halt` observes a halt during that replay.

## How to get to it (user POV)

- From the sortaquant repo root, run the simulation snippet in the README and in `docs/05_RELEASE_VALIDATION.md` (all BBOs, then all trades, symbol `BTCFDUSD`, maker fee 0).
- There is no other entry point. Paper mode opens a live Binance socket and is not this feature.

## Driving it with sqv

Preconditions:

- Doctor reports version `0.1.0` and `"network": "not contacted"`.
- `--run-id` is unused by a live `sqv.py` process.

- **Replay.** Run `uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py simulate --run-id <run-id>`. Exit code `0`. `artifacts/<run-id>/simulation/result.json` has `"ok": true`, `"bbo_count": 500`, `"tape_trade_count": 250`, `"loop": "all bbos, then all trades"`.
- **Zero fee.** The same file has `"symbol": "BTCFDUSD"`, `"maker_fee_bps": 0.0`, and `"maker_fees_paid": 0.0`.
- **Regimes.** `"regimes"` is `["R1_CALM", "R2_TRENDING", "R3_HIGHVOL", "R4_BREAK"]`.
- **Halt and fill.** `"halt_observed": true` and `"total_trades": 1`. Seed 42 produces one fill on this loop.
- **Proof.** Leave `result.json` in place. It is the action and the resulting summary.

## Gotchas

- Streaming every trade after every BBO is the documented operator loop. Interleaving trades inside the BBO loop is a different path and does not satisfy this recipe.
- `--seed`, `--ticks`, `--symbol`, or `--maker-fee-bps` other than the defaults skip the fill, halt, and regime assertions. The canonical proof uses the defaults.
- `total_net_pnl` of `0.0` is not the proof. The proof is the fill count, the zero fee, the four regimes, and the halt.
- A halt flag read off a private attribute is not this drive. `sqv.py` uses `RegimeEngine.posterior` and `RegimeEngine.is_halted`.

# Inventory clamp

Once a maker buy fills up to `Q_max`, the next quote cancels the bid and keeps the ask.

## Sub-features

- `clamp-fill` applies one maker buy of 0.5 BTC at 60000.
- `clamp-bid` cancels the bid because position is at or above `Q_max`.
- `clamp-ask` leaves the ask price in place.

## How to get to it (user POV)

- A fill arrives through `DeterministicRiskGovernor.on_fill`.
- The next decision is `evaluate_quotes` on a two-sided quote.
- The operator-visible result is a missing bid, a live ask, and a reason that starts with `CLAMP:`.

## Driving it with sqv

Preconditions:

- Doctor reports version `0.1.0`.
- `--run-id` is unused by a live `sqv.py` process.
- The drive creates its own governor. It does not attach to another process.

- **Fill to the ceiling.** Run `uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py inventory-clamp --run-id <run-id>`. Exit code `0`. `artifacts/<run-id>/inventory-clamp/result.json` has `"ok": true` and `"position"` greater than or equal to `"q_max"`.
- **Bid cancelled.** `"bid"` is `null` and some `"reasons"` entry starts with `CLAMP:`.
- **Ask remains.** `"ask"` is a number and `"halted"` is `false`.
- **Proof.** Position, `q_max`, both prices, and the reasons are in that file.

## Gotchas

- Writing `current_position` from outside `on_fill` is not this path. The drive sends a `Fill`.
- A full halt (`"halted": true` with both prices null) is the stale-feed or drawdown path, not a successful clamp.
- Depth and volatility are fixed inside the command (mid 60000, volatility 0.01, depth 10). Changing them changes `Q_max`, and a 0.5 BTC buy may no longer reach it. This recipe does not take those as flags.

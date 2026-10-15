# Stale-feed halt

A feed gap longer than 500ms cancels both quotes. A 100ms gap does not.

## Sub-features

- `stale-fresh` keeps a two-sided quote 100ms after the last packet.
- `stale-gap` cancels the bid and the ask 600ms after that same packet, with a stale-feed reason.

## How to get to it (user POV)

- The operator guide states that a WebSocket gap over 500ms cancels all quotes.
- The call sequence is `DeterministicRiskGovernor.on_feed_packet`, then `evaluate_quotes` at a later timestamp.

## Driving it with sqv

Preconditions:

- Doctor reports version `0.1.0`.
- `--run-id` is unused by a live `sqv.py` process.

- **Fresh gap.** Run `uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py stale-feed --run-id <run-id>`. Exit code `0`. `artifacts/<run-id>/stale-feed-halt/result.json` has `"fresh_halted": false`, and both `"fresh_bid"` and `"fresh_ask"` are numbers.
- **Stale gap.** The same file has `"stale_halted": true`, `"stale_bid": null`, `"stale_ask": null`, `"gap_ms": 600`, and a `stale_reasons` entry that contains `Stale feed`.
- **Proof.** Both the fresh quote and the cancelled quote are in that file. The cancel alone is not enough, because a governor that always halts would also cancel.

## Gotchas

- The timeout is 500ms. A 400ms gap must not be treated as this recipe's stale case.
- `evaluate_quotes` does not refresh the watchdog. Only `on_feed_packet` does. A second packet at the later timestamp hides the gap.
- Assigning `is_drawdown_halted = True` is the manual kill switch in the operator guide. It is a different feature and this recipe does not set it.

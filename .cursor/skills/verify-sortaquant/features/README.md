# Sortaquant verification map

This directory is the maintained source for verifying operator-facing behavior of sortaquant. Read this index, then use the matching feature file as the recipe.

## Baseline preconditions

- Working directory is the sortaquant repo root (`pyproject.toml` name `sortaquant`).
- `uv sync --extra dev` has been run and `uv run python -c "import sortaquant; print('ready')"` prints `ready`.
- `sqv.py doctor` reports version `0.1.0`, hello `Hello from sortaquant!`, and `"network": "not contacted"`.
- Each run uses a fresh `--run-id`. Drives do not share memory or a port.
- Do not construct `BinanceWebSocketAdapter`. Verification stays on the in-process synthetic book and the Gate 1 presets.

## Driving conventions

- Start every recipe from the baseline above.
- Treat every command as literal. Keep flags and the run id unchanged within one recipe.
- Run every command through `uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py`.
- Pass means exit code `0` and `"ok": true` in `artifacts/<run-id>/<feature>/result.json`.
- Do not delete proof artifacts during cleanup.

## Proof and skip reporting

- Capture the command's resulting state from `result.json`, not only the exit code.
- Record the feature id and the `--run-id` with every artifact.
- Report an unreachable path with the command you ran and the unmet precondition.
- Do not report a skipped entry point as verified through a different command.
- Live Binance paper trading is a documented operator mode. This map does not verify it. Say so when a change only affects that path.

## Feature entry contract

Each feature file starts with an H1 and one paragraph, then exactly four H2 sections: `Sub-features`, `How to get to it (user POV)`, `Driving it with sqv`, and `Gotchas`.

## Features

- [Tick simulation](./simulation.md) replays the synthetic 500-tick book and reports fills, fees, regimes, and halt.
- [Gate 1 venue audit](./gate1-audit.md) accepts or rejects the preset venues on the half-spread hurdle.
- [Stale-feed halt](./stale-feed-halt.md) keeps quotes after 100ms and cancels both sides after 600ms.
- [Inventory clamp](./inventory-clamp.md) cancels the bid once a maker buy reaches `Q_max`, and leaves the ask up.

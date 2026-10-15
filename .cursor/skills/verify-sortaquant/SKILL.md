---
name: verify-sortaquant
description: "Drive the sortaquant RAIM library (operator Python API, no UI) through synthetic tick simulation, the Gate 1 venue audit, stale-feed cancel, and inventory clamp, and write proof JSON. Use when a sortaquant change needs proof that those operator paths still hold, or when launching, checking, or tearing down a verification run."
---

# Verify sortaquant

Sortaquant is a Python library. An operator runs it with `uv run python` from the repo root. There is no web UI, no port, and no long-lived server. Each drive is its own process with its own `--run-id`. Two runs can proceed together. Do not open `BinanceWebSocketAdapter` or any other live socket during verification. That path shares Binance with whatever else the machine is doing, and this skill refuses it.

`pytest` is the regression suite. It is not this harness. Proof comes from `sqv.py`.

## Launch

From the sortaquant repo root:

```bash
uv sync --extra dev
uv run python -c "import sortaquant; assert sortaquant.hello() == 'Hello from sortaquant!'; print('ready')"
```

Ready means stdout is `ready` and the exit code is 0. A failed import means the environment is not usable yet. The sync step installs the package and its dependencies into `.venv`. No database and no seed files. The synthetic book is generated inside each drive.

## Doctor

Read-only. Run this first whenever a drive looks wrong. It does not write artifacts and does not open a socket.

```bash
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py doctor
```

Require `"ok": true`, `"version": "0.1.0"`, `"hello": "Hello from sortaquant!"`, `"network": "not contacted"`, and `"repo"` equal to this checkout. Python must be 3.11 or newer. Exit code `0`. If any of that fails, stop. Do not drive a broken install.

## Drive

The harness is `sqv.py`. Invoke it with `uv run` so the repo venv is the one that imports `sortaquant`. Replace `<run-id>` with a new id per run (`verify-20261015-1`). Do not reuse an id that still has a live process.

Canonical simulation, matching the operator guide (every BBO, then every trade):

```bash
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py simulate --run-id <run-id>
```

Gate 1 venue audit (in-process presets, no network):

```bash
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py gate1 --run-id <run-id>
```

Stale-feed cancel, then inventory clamp:

```bash
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py stale-feed --run-id <run-id>
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py inventory-clamp --run-id <run-id>
```

Exit code `0` and `"ok": true` are the pass signal. The feature files under `features/` are the recipes. Read `features/README.md` before driving, then follow one feature file. A proof that only runs `simulate` does not cover Gate 1, the stale-feed halt, or the inventory clamp.

## Evidence

Artifacts live at `.cursor/skills/verify-sortaquant/artifacts/<run-id>/<feature>/result.json`. Doctor does not write a file. Save its stdout yourself:

```bash
mkdir -p .cursor/skills/verify-sortaquant/artifacts/<run-id>/doctor
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py doctor \
  > .cursor/skills/verify-sortaquant/artifacts/<run-id>/doctor/doctor.json
```

Each `result.json` records the command and the resulting state. Pass means the user-visible outcome is in that file, not only that the process exited.

Proof standards:

- Drive the operator API (`TickSimulator.step_bbo`, `step_trade`, `summarize`, `Gate1HurdleValidator.audit_venues`, `DeterministicRiskGovernor.on_feed_packet`, `on_fill`, `evaluate_quotes`). Do not call test fixtures, and do not assign `is_drawdown_halted`.
- Capture the action and the resulting state. For simulation that is tick count, fill count, fees, regimes, and whether a halt was observed. For Gate 1 it is each venue's `passed` flag and net edge. For the governor it is both sides of the quote before and after the trigger.
- Side effects to check: `result.json` exists after the process exits, and `maker_fees_paid` is `0` on the zero-fee pair. These drives do not write orders, database rows, or git refs.
- The synthetic book is generated in-process from a fixed seed. It is not a mock of an exchange. Live Binance is out of scope. `audit_venues` does not touch the network. Confirm `"network": "not contacted"` on doctor and on the Gate 1 result. Do not treat a drive that opened a websocket as a dry run.

## Cleanup

```bash
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py cleanup --run-id <run-id>
```

Cleanup reads `/tmp/sortaquant-verify-<run-id>/pid`. It sends `SIGTERM` only when that pid is still alive and its command line contains both `sqv.py` and the same `<run-id>`. It then deletes that scratch directory. It does not delete `.cursor/skills/verify-sortaquant/artifacts/`. After cleanup, `result.json` for the run must still be there.

There is nothing to kill by process name. A finished drive has already removed its pid file. Cleanup of a finished run only removes an empty scratch dir, if one remains.

## Helpers

`scripts/sqv.py` is the only helper. It is executable. From the repo root:

```bash
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py --help
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py doctor
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py simulate --run-id <run-id>
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py gate1 --run-id <run-id>
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py stale-feed --run-id <run-id>
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py inventory-clamp --run-id <run-id>
uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py cleanup --run-id <run-id>
```

`simulate` defaults are `--ticks 500 --seed 42 --symbol BTCFDUSD --maker-fee-bps 0`. Those defaults are the canonical proof. Other seeds still check that the BBO count matches `--ticks` and that a zero fee stays zero, and they skip the seed-42 fill, halt, and regime assertions.

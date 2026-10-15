# Gate 1 venue audit

Gate 1 tells an operator which preset venues clear the hurdle `maker fee + expected adverse selection < half the typical spread`, and which must be rejected.

## Sub-features

- `gate1-reject-vip0` rejects Binance spot BTC/USDT at VIP 0.
- `gate1-pass-fdusd` accepts Binance spot BTC/FDUSD at a zero maker fee, with net edge `0.35` bps.
- `gate1-reject-perp-vip0` rejects the BTCUSDT perp at VIP 0.
- `gate1-pass-rebate` accepts the BTCUSDT perp market-maker rebate tier.
- `gate1-pass-sol` accepts the SOLUSDT perp at VIP 1.
- `gate1-offline` performs the audit without contacting a venue.

## How to get to it (user POV)

- Run `Gate1HurdleValidator.audit_venues()` and read each result's `message`, as in the paper-mode block of `docs/05_RELEASE_VALIDATION.md`.
- That block imports `BinanceWebSocketAdapter` and does not connect it. This recipe does not import it either.

## Driving it with sqv

Preconditions:

- Doctor reports `"network": "not contacted"`.
- `--run-id` is unused by a live `sqv.py` process.

- **Audit.** Run `uv run python .cursor/skills/verify-sortaquant/scripts/sqv.py gate1 --run-id <run-id>`. Exit code `0`. `artifacts/<run-id>/gate1-audit/result.json` has `"ok": true` and `"network": "not contacted"`.
- **Reject VIP 0 spot.** `venues.binance_spot_btcusdt_vip0.passed` is `false`.
- **Accept FDUSD.** `venues.binance_spot_btcfdusd_zerofee.passed` is `true` and `net_edge_bps` is `0.35`.
- **Perp split.** `venues.binance_perp_btcusdt_vip0.passed` is `false`. `venues.binance_perp_btcusdt_mm_rebate.passed` is `true`. `venues.binance_perp_solusdt_vip1.passed` is `true`.
- **Proof.** The five `message` strings are in that same file. They are the operator-visible audit.

## Gotchas

- A passing FDUSD row does not prove the other four presets. All five flags have to be in the file.
- This command never opens a websocket. A run that connected to Binance is not a Gate 1 proof.
- Net edge is in basis points. `0.350` and `0.35` are the same value. A different number is a failure.

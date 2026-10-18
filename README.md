# sortaquant

Regime-adaptive, inventory-aware market making (RAIM) engine for high-frequency crypto limit order books.

## Architecture

Two strictly decoupled layers:

- **Fast Body (deterministic, sub-millisecond)**: WebSocket L2 book and trade ingestion, feature extraction (bipower variation, multi-level OFI, VPIN, variance ratio, Hawkes branching), 4-state HMM forward filter + Bayesian Online Changepoint Detection (BOCPD), inventory-aware quoting (Avellaneda-Stoikov / Guéant-Lehalle), and deterministic risk governance.
- **Slow Brain (polled, seconds-to-minutes)**: Isolated macro context via `tradingview-mcp` and Claude Opus 5.5. Outputs schema-validated bounds `(z_VIX, TrendAlign, VolExpansion, EventRisk, UniverseScore, risk_multiplier, confidence)`. Never prices or touches the order loop; confidence decays exponentially on feed failure.

The risk governor enforces `m_applied = min(m_LLM, m_rules)`. The LLM can only tighten risk bounds, never loosen them.

## Gate 1: Economic Viability

A market maker cannot operate if fee drag and adverse selection exceed half the spread:

$$\text{maker\_fee} + \mathbb{E}[\text{adverse\_selection}] < \frac{1}{2} \bar{S}_{\text{typical}}$$

- **Binance BTC/USDT (VIP 0)**: 7.5 bps fee vs 0.0015 bps spread -> **disqualified** (negative edge of -7.50 bps).
- **Binance BTC/FDUSD (Zero maker fee)**: 0.0 bps fee, ~1.2 bps spread, ~0.25 bps adverse selection -> **target pair** (+0.35 bps net edge).
- **Binance Perp (MM rebate tier)**: -0.5 bps rebate, ~0.025 bps spread -> **viable** (+0.50 bps net edge).

## Package Layout

```
sortaquant/
├── core/         # L2 orderbook, BBO, types, event data structures
├── features/     # Bipower variation, OFI, VPIN, variance ratio, Hawkes
├── regime/       # 4-state HMM (Baum-Welch + forward filter), BOCPD override
├── quoting/      # Micro-price, Avellaneda-Stoikov, Guéant-Lehalle
├── risk/         # Governor, Q_max ceiling, 500ms stale-feed watchdog, token bucket
├── slow_brain/   # TradingView MCP client, Pydantic schema, confidence decay
├── adapters/     # Binance WebSocket client, deterministic replay
├── runtime/      # Paper trading session (live book, virtual fills)
├── sim/          # Discrete-event tick simulator (FIFO queue priority, 2x latency stress)
└── validation/   # Gate 1 auditor, markout reports, purged walk-forward CV
```

## How to use it

Requires Python >= 3.11 and [`uv`](https://astral.sh/uv).

```bash
uv sync --extra dev
uv run pytest
```

**Simulate** (offline synthetic tape):

```bash
uv run sortaquant simulate --symbol BTCFDUSD --ticks 500
```

**Paper trade** (live Binance book, virtual fills, **no orders sent**):

```bash
# Liquid tape (best for watching the engine live)
uv run sortaquant paper --symbol BTCUSDT --duration 60

# Gate 1 target pair (zero maker fee; quieter trade tape)
uv run sortaquant paper --symbol BTCFDUSD --duration 60
```

Market data defaults to `data-stream.binance.vision` (avoids geo blocks on `stream.binance.com`). Override with `--data-host` if needed.

**Gate 1 venue audit** (static fee/spread table):

```bash
uv run sortaquant audit
```

**Doctor** (imports, Gate 1, simulate smoke, live feed probe):

```bash
uv run sortaquant doctor
uv run sortaquant doctor --offline   # skip live feed
```

**Walk-forward** (purged + embargoed, HMM fitted on train):

```bash
uv run sortaquant walkforward --ticks 500 --json-out /tmp/wf.json
```

Paper prints a heartbeat every 5s (`--heartbeat 0` to disable) and accepts `--json-out` for a run report. It does not place, cancel, or amend exchange orders. Live trading is not shipped.

## Key Invariants

1. **Deterministic Q_max**: Position cap is $\min(B / (z \sigma \sqrt{h}), c \cdot \text{depth})$. Quotes that would breach $Q_{\max}$ are clamped or cancelled immediately.
2. **Stale Feed Watchdog**: Cancel-all dispatched if WebSocket packets gap by $> 500\text{ms}$.
3. **Emergency Hysteresis**: Quotes halt when $\pi(R4) > 0.70$ or BOCPD changepoint alarm fires; resume only when $\pi(R4) < 0.40$ for 30 consecutive ticks.
4. **Queue Realism**: Fills obey FIFO queue priority with ahead-volume consumption and proportional cancel degradation. Tested under $2\times$ latency stress.
5. **Markout Attribution**: Forward adverse selection is tracked at $t+100\text{ms}, t+1\text{s}, t+5\text{s}, t+30\text{s}$. If 4 consecutive fills show adverse markout, quoting pauses for 30s.

## References

- Avellaneda & Stoikov (2008). *High-frequency trading in a limit order book*.
- Guéant, Lehalle & Fernandez-Tapia (2013). *Dealing with the inventory risk*.
- Cont, Kukanov & Stoikov (2014). *The Price Impact of Order Book Events*.
- Stoikov (2018). *The Micro-Price: A High-Frequency Estimator of Future Prices*.
- Easley, Lopez de Prado & O'Hara (2012). *Flow Toxicity and Liquidity in a High-Frequency World (VPIN)*.
- Barndorff-Nielsen & Shephard (2004). *Power and Bipower Variation with Stochastic Volatility and Jumps*.
- Adams & MacKay (2007). *Bayesian Online Changepoint Detection*.

## License

MIT

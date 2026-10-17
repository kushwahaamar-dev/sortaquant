# RAIM Gate 6: Release Validation & Operator Guide

## 1. Release Status & Quality Summary

The RAIM (Regime-Adaptive, Inventory-Aware Market Making) engine has passed all six development gates:

| Gate | Title | Deliverables | Verification Status |
| :--- | :--- | :--- | :--- |
| **Gate 1** | Requirements & Economic Viability | `docs/01_REQUIREMENTS_AND_GATE1.md`, `gate1.py` | ✅ **PASSED** (Hurdle verified on BTC/FDUSD) |
| **Gate 2** | Architecture Specification | `docs/02_ARCHITECTURE.md` | ✅ **PASSED** (Decoupled Fast/Slow layers) |
| **Gate 3** | Test-First Implementation Plan | `docs/03_PLAN.md` | ✅ **PASSED** (Full 8-phase TDD plan) |
| **Gate 4** | Engine Build & Test Suite | `src/sortaquant/`, 39 tests in `tests/` | ✅ **PASSED** (39/39 tests passing, 87% coverage) |
| **Gate 5** | Independent Review ("What could I be wrong about?") | `docs/04_INDEPENDENT_REVIEW.md` | ✅ **PASSED** (10 failure modes mitigated) |
| **Gate 6** | Release Validation & Operator Guide | `docs/05_RELEASE_VALIDATION.md` | ✅ **APPROVED FOR SIMULATION & PAPER** (no live order entry) |

---

## 2. Environment Setup & Quickstart

### Prerequisites
- Python >= 3.11 (tested on Python 3.12 / 3.14)
- `uv` package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Installation
```bash
git clone https://github.com/kushwahaamar-dev/sortaquant.git
cd sortaquant

# Synchronize virtual environment with all dependencies
uv sync --extra dev
```

### Running the Test Suite
```bash
# Run all 39 unit and integration tests
uv run pytest

# Run with full coverage report
uv run pytest --cov=sortaquant --cov-report=term-missing
```

---

## 3. Operating Modes: Simulation -> Paper -> Live

By design, RAIM supports **SIMULATION** and **PAPER**. Live order placement is not shipped.

### 1. Simulation Mode
```bash
uv run sortaquant simulate --symbol BTCFDUSD --ticks 500
```

### 2. Paper Mode (live book, virtual execution)
```bash
uv run sortaquant paper --symbol BTCFDUSD --duration 60
```
This opens the Binance combined depth+trade stream, drives `TickSimulator`, and never sends orders.

### 3. Slow Brain MCP Server Setup
The Slow Brain analysis layer runs independently via the official PyPI package:
```bash
# Run TradingView MCP server via uvx
uvx --from tradingview-mcp-server tradingview-mcp stdio
```

---

## 4. Emergency Procedures & Kill Switches

### Stale-Feed Automatic Trigger
If WebSocket data halts for $> 500\text{ ms}$, the engine automatically cancels all quotes and raises an alarm.

### Drawdown Circuit Breaker
If intraday portfolio equity drops by $\ge 2.0\%$ from the high-water mark, all quotes are cancelled and the system enters locked halt.

### Operator Manual Kill Switch
To manually halt all quoting immediately:
```python
governor.halt("operator")
```
This forces all bids and asks to `None` and clamps quoting sizes to zero.

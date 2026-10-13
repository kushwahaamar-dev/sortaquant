# RAIM Gate 3: Test-First Build Plan (TDD Roadmap)

## Phase 1: Core Foundation & Economic Hurdle (Day 1)
- [x] **Task 1.1**: Define fundamental domain types (`Order`, `Trade`, `BookLevel`, `Side`, `RegimeState`).
- [x] **Task 1.2**: Implement in-memory L2 Limit Order Book with microsecond price-level lookups, BBO calculation, and depth summation.
- [x] **Task 1.3**: Implement Gate 1 Hurdle Validator (`maker_fee + E[AS] < 0.5 * typical_spread`) with automated venue feasibility check.
- [x] **Test Suite 1**: `tests/test_gate1.py` & `tests/test_orderbook.py`.

## Phase 2: Microstructure Feature Extraction (Day 1-2)
- [x] **Task 2.1**: Bipower Variation Volatility (`sortaquant/features/bipower.py`): Jump-robust continuous volatility estimator (Barndorff-Nielsen & Shephard 2004).
- [x] **Task 2.2**: Order Flow Imbalance (`sortaquant/features/ofi.py`): Level-1 & multi-level OFI (Cont, Kukanov, Stoikov 2014).
- [x] **Task 2.3**: VPIN (`sortaquant/features/vpin.py`): Volume-bucketed toxicity index with tick-rule trade classification.
- [x] **Task 2.4**: Multi-horizon Variance Ratio (`sortaquant/features/variance_ratio.py`): Lo-MacKinlay mean-reversion test.
- [x] **Task 2.5**: Hawkes Self-Exciting Process (`sortaquant/features/hawkes.py`): Fast branching ratio $n = \alpha/\beta$ for trade clusters.
- [x] **Test Suite 2**: `tests/test_features.py`.

## Phase 3: 4-State HMM & BOCPD Regime Engine (Day 2)
- [x] **Task 3.1**: 4-State HMM (`sortaquant/regime/hmm.py`): Baum-Welch offline trainer and forward filter with log-sum-exp numerical stability.
- [x] **Task 3.2**: Macro Transition Tilting: Modulates transition matrix $A_{jk}$ via Slow Brain $(z_{\text{VIX}}, \text{EventRisk})$.
- [x] **Task 3.3**: Bayesian Online Changepoint Detection (`sortaquant/regime/bocpd.py`): Adams & MacKay (2007) zero-latency structural break override.
- [x] **Task 3.4**: Regime Blending & Hysteresis (`sortaquant/regime/engine.py`): Pull quotes when $\pi(R4) > 0.7$, resume below $0.4$.
- [x] **Test Suite 3**: `tests/test_regime.py`.

## Phase 4: Inventory-Aware Quoting Engine (Day 3)
- [x] **Task 4.1**: Stoikov Micro-price & OFI Fair Value (`sortaquant/quoting/microprice.py`).
- [x] **Task 4.2**: Avellaneda-Stoikov reservation price & Guéant-Lehalle optimal half-spreads (`sortaquant/quoting/gueant_lehalle.py`).
- [x] **Task 4.3**: Regime-adaptive spread widening, asymmetric flow fading, and size reduction (`sortaquant/quoting/engine.py`).
- [x] **Test Suite 4**: `tests/test_quoting.py`.

## Phase 5: Deterministic Risk Governor (Day 3-4)
- [x] **Task 5.1**: Dynamic inventory limit $Q_{\max} = \min(B / (z \sigma \sqrt{h}), c \cdot \text{depth})$.
- [x] **Task 5.2**: Watermark drawdown circuit breaker and loss-streak pause.
- [x] **Task 5.3**: Stale-feed watchdog with 500ms heartbeat cancel-all trigger.
- [x] **Task 5.4**: Token bucket rate limiter with exponential backoff on reject/HTTP 429.
- [x] **Task 5.5**: Asymmetric LLM rule enforcement: $m_{\text{applied}} = \min(m_{\text{LLM}}, m_{\text{rules}})$.
- [x] **Test Suite 5**: `tests/test_risk_governor.py`.

## Phase 6: Slow Brain TradingView MCP Client & Isolation (Day 4)
- [x] **Task 6.1**: Pydantic schema validation for Claude Opus 5.5 output (`sortaquant/slow_brain/schema.py`).
- [x] **Task 6.2**: Polled TradingView MCP client adapter with 60s cache and $\ge 0.8$s spacing (`sortaquant/slow_brain/client.py`).
- [x] **Task 6.3**: Error envelope handling with exponential confidence decay $c = \exp(-\Delta t / \tau)$ (`sortaquant/slow_brain/adapter.py`).
- [x] **Test Suite 6**: `tests/test_slow_brain.py`.

## Phase 7: Tick-Level Realistic Simulator (Day 5)
- [x] **Task 7.1**: FIFO queue priority tracking and cancel degradation model (`sortaquant/sim/queue_model.py`).
- [x] **Task 7.2**: Lognormal latency modeling with 2x stress test mode (`sortaquant/sim/latency_model.py`).
- [x] **Task 7.3**: Discrete-event tick simulator with fee, rebate, and fill accounting (`sortaquant/sim/simulator.py`).
- [x] **Test Suite 7**: `tests/test_simulator.py`.

## Phase 8: Walk-Forward Validation & End-to-End Harness (Day 5)
- [x] **Task 8.1**: Purged and embargoed walk-forward backtest harness (`sortaquant/validation/walk_forward.py`).
- [x] **Task 8.2**: Markout attribution reports ($t+100\text{ms}, t+1\text{s}, t+5\text{s}, t+30\text{s}$).
- [x] **Task 8.3**: Full end-to-end integration test with live market replay.
- [x] **Test Suite 8**: `tests/test_walk_forward.py` & `tests/test_end_to_end.py`.

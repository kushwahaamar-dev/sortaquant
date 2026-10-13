# RAIM Gate 2: Architecture Specification

## 1. System Overview

The RAIM (Regime-Adaptive, Inventory-Aware Market Making) system is partitioned into two strictly separated, asynchronous layers:

```
+-----------------------------------------------------------------------------------------+
|                                    SLOW BRAIN                                           |
|                           (Seconds to Minutes, Non-Blocking)                            |
|                                                                                         |
|   +-----------------------+       +-------------------+       +---------------------+   |
|   |  TradingView MCP      |  -->  | Claude Opus 5.5   |  -->  | Schema Validator    |   |
|   |  (Polled, 60s Cache,  |       | Macro/Regime      |       | (Pydantic, Fail-Safe|   |
|   |   >=0.8s spacing)     |       | Synthesis         |       |  Confidence Decay)  |   |
|   +-----------------------+       +-------------------+       +---------------------+   |
+---------------------------------------------|-------------------------------------------+
                                              | m_LLM in [0, 1], z_VIX, EventRisk
                                              v
+-----------------------------------------------------------------------------------------+
|                                    FAST BODY                                            |
|                           (Deterministic Tick Loop, < 1ms)                              |
|                                                                                         |
|   +-------------------+       +-------------------+       +-------------------------+   |
|   | Exchange L2 Books |  -->  | Microstructure    |  -->  | 4-State HMM + BOCPD     |   |
|   | & Trade WebSocket |       | Feature Extractor |       | Regime Posterior pi(t)  |   |
|   +-------------------+       +-------------------+       +-------------------------+   |
|                                                                         |               |
|                                                                         v               |
|   +-------------------+       +-------------------+       +-------------------------+   |
|   | Exchange Orders   |  <--  | Deterministic     |  <--  | Optimal Quoting Engine  |   |
|   | (Post-Only FSM)   |       | Risk Governor     |       | (Avellaneda-Stoikov +   |   |
|   |                   |       | m = min(m_LLM,m_R)|       |  Gueant-Lehalle + OFI)  |   |
|   +-------------------+       +-------------------+       +-------------------------+   |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Fast Body Components (Deterministic Core)

### 2.1 Microstructure Feature Extraction
Every incoming tick or book delta updates a high-efficiency circular buffer:
1. **Bipower Variation Volatility ($BV_t$)**:
   Jump-robust volatility estimator from Barndorff-Nielsen & Shephard (2004):
   $$BV_t = \frac{\pi}{2} \sum_{i=2}^N |r_i| |r_{i-1}|$$
   Filters out Poisson jumps to isolate continuous diffusive volatility $\sigma_{\text{diff}}$.
2. **Spread & Imbalance**:
   Relative spread $S_t = \frac{P_{a,0} - P_{b,0}}{M_t}$ and multi-level depth imbalance $I_t = \frac{\sum_{k=1}^K V_{b,k} - \sum_{k=1}^K V_{a,k}}{\sum_{k=1}^K V_{b,k} + \sum_{k=1}^K V_{a,k}}$.
3. **Order Flow Imbalance ($OFI_t$)**:
   Cont, Kukanov & Stoikov (2014) price impact metric:
   $$OFI_t = \Delta L_{b,t} - \Delta L_{a,t}$$
   where $\Delta L_{b,t} = V_{b,t} \cdot \mathbb{I}_{P_{b,t} \ge P_{b,t-1}} - V_{b,t-1} \cdot \mathbb{I}_{P_{b,t} \le P_{b,t-1}}$.
4. **Volume-Synchronized Probability of Toxicity ($VPIN_t$)**:
   Easley, Lopez de Prado & O'Hara (2012) volume-bucketed toxicity index:
   $$VPIN = \frac{\sum_{\tau=1}^N |V_\tau^B - V_\tau^S|}{N \cdot V_{\text{bucket}}}$$
5. **Variance Ratio ($VR_t$)**:
   Lo-MacKinlay multi-horizon ratio testing mean reversion ($VR < 1$) versus trending flow ($VR > 1$).
6. **Hawkes Self-Excitation Ratio ($n_t$)**:
   Hawkes branching ratio $n = \frac{\alpha}{\beta}$ from trade arrival clusters.

---

### 2.2 Regime Detection Engine (4-State HMM + BOCPD)
The regime engine estimates posterior probabilities across four regimes:
- **R1: Calm / Mean-Reverting** (Low vol, tight spread, low VPIN, $VR < 1$)
- **R2: Trending / Informed** (Persistent OFI, directional drift, $VR > 1$, high Hawkes $n$)
- **R3: High-Vol / Stress** (Elevated bipower variation, widening spreads, volatile depth)
- **R4: Structural Break / Flash Crash** (Violent jump, book depletion, VPIN spike)

#### Online Forward Filter with Transition Tilting
Transition matrix $A_{jk}(t)$ is dynamically tilted by Slow Brain state $(z_{\text{VIX}}, \text{EventRisk})$:
$$A_{jk}(t) = \frac{A_{jk}^0 \cdot \exp(\gamma_{\text{macro}} \cdot \mathbf{w}_{jk}^\top \mathbf{x}_{\text{slow}})}{\sum_l A_{jl}^0 \cdot \exp(\gamma_{\text{macro}} \cdot \mathbf{w}_{jl}^\top \mathbf{x}_{\text{slow}})}$$
The online posterior is updated via log-sum-exp stabilized forward filtering:
$$\pi_k(t) \propto f_k(\mathbf{x}_t) \sum_j \pi_j(t-1) A_{jk}(t)$$

#### Bayesian Online Changepoint Detection (BOCPD)
Adams & MacKay (2007) BOCPD runs concurrently to detect structural breaks within single ticks, bypassing HMM inertia to trip the emergency circuit breaker when changepoint probability exceeds $p_{\text{alarm}} > 0.85$.

#### Discrete Action Hysteresis
To prevent quote flicker during regime transitions:
- Quote Pull (Halt): Triggered when $\pi(R4) > 0.70$.
- Quote Resume: Allowed only when $\pi(R4) < 0.40$ for at least $K_{\text{confirm}} = 50$ consecutive ticks.

---

### 2.3 Optimal Quoting Engine

#### Fair Value Estimation
Fair value combines Stoikov (2018) micro-price with OFI short-horizon predictive drift:
$$P_{\text{micro}} = M_t + \left(\frac{V_b - V_a}{V_b + V_a}\right) \frac{S_t}{2}$$
$$\hat{v}_t = P_{\text{micro}} + \beta_{\text{OFI}} \cdot OFI_t$$

#### Avellaneda-Stoikov & Guéant-Lehalle Optimal Spreads
Inventory penalty and optimal half-spreads follow Guéant, Lehalle & Fernandez-Tapia (2013):
$$\text{Reservation Price: } r(s, q, t) = \hat{v}_t - q \gamma \sigma^2 (T - t)$$
$$\text{Optimal Half-Spreads: } \delta^a(q), \delta^b(q) = \frac{1}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right) + \frac{(2q \pm 1) \gamma \sigma^2}{2 \kappa}$$

#### Regime Blending & Parameter Adaptation
Quotes blend regime-specific risk aversions $\gamma_k$ and spread markups:
$$\gamma_{\text{eff}} = \sum_{k=1}^4 \pi_k(t) \gamma_k + \mathbb{I}_{\pi(R3) > 0.3} \cdot \Delta \gamma_{\text{stress}}$$
- **In R1**: Symmetric two-sided quoting tightly bounding $\hat{v}_t$.
- **In R2**: Asymmetric quote fading (fade adverse side, skew inventory release towards trend direction).
- **In R3**: Widen spreads by $2\times - 3\times$, scale down quote sizes by $0.3\times$.
- **In R4**: Discrete action: pull all quotes immediately.

---

### 2.4 Deterministic Risk Governor

The Risk Governor sits strictly **outside and above** all machine learning and LLM logic. It has absolute authority to clamp, widen, or cancel orders.

1. **Dynamic Inventory Ceiling ($Q_{\max}$)**:
   $$Q_{\max} = \min\left(\frac{B}{z \cdot \sigma \sqrt{h_{\text{unwind}}}}, c \cdot \text{Depth}_{\text{L2}}\right)$$
   where $B$ is capital risk budget, $z = 2.58$ (99% VaR), $\sigma$ is short-term volatility, and $h_{\text{unwind}}$ is estimated unwind half-life.
2. **Hard Position Limits**: Clamps active quoting size so $|q + \Delta q| \le Q_{\max}$.
3. **High-Water Mark Drawdown Circuit Breaker**:
   If intraday loss exceeds $\text{MaxDrawdown}_{\text{limit}}$ (e.g. 2.0% of allocated capital), execute immediate flat-and-halt.
4. **Stale-Feed Watchdog**:
   If time since last exchange WebSocket packet $\Delta t_{\text{feed}} > 500\text{ ms}$, fire immediate **Cancel-All** and suspend quoting until feed is verified.
5. **Token Bucket Rate Limiter**:
   Enforces maximum 20 messages per second with exponential backoff on exchange HTTP/WS 429 or order reject events.
6. **Loss-Streak Circuit Breaker**:
   If 4 consecutive fills suffer adverse markout at $t + 1\text{s}$, widen spreads by $50\%$ and pause for 30 seconds.
7. **The Asymmetric LLM Rule**:
   $$m_{\text{applied}} = \min(m_{\text{LLM}}, m_{\text{rules}})$$
   The LLM risk multiplier $m_{\text{LLM}} \in [0, 1]$ can **only tighten risk** ($m < 1$). It can never loosen hard limits, increase position caps, or un-halt a circuit breaker.

---

## 3. Slow Brain Components (Isolated & Polled)

### 3.1 TradingView MCP Integration
- **Package**: `tradingview-mcp` via official PyPI `tradingview-mcp-server`.
- **Operating Constraints**:
  - Polled at low frequency (nominal: 60s cache; hard limit: max 4 concurrent requests, $\ge 0.8\text{s}$ spacing).
  - No direct tick access, no order placement capability.
- **Fail-Safe Error Envelope**:
  Responses conforming to `{"error": "code", "retry"}` are handled gracefully:
  - If retryable: backoff exponentially and hold last verified state.
  - If persistent outage: decay confidence exponentially:
    $$c(t) = c_0 \exp\left(-\frac{\Delta t}{\tau}\right) \quad (\tau = 300\text{s})$$
    When $c(t) < 0.20$, Slow Brain state reverts smoothly to neutral uninformative prior ($m = 1.0, z_{\text{VIX}} = 0$).

### 3.2 Claude Opus 5.5 Pydantic Schema
```python
class SlowBrainState(BaseModel):
    z_VIX: float = Field(ge=-5.0, le=5.0, description="VIX / Volatility index z-score")
    TrendAlign: int = Field(ge=-1, le=1, description="Macro trend alignment: -1 (bearish), 0 (neutral), 1 (bullish)")
    VolExpansion: int = Field(ge=0, le=1, description="1 if volatility expansion cycle detected, 0 otherwise")
    EventRisk: int = Field(ge=0, le=1, description="1 if high-impact macro or exchange event imminent, 0 otherwise")
    UniverseScore: float = Field(ge=0.0, le=1.0, description="Cross-asset liquidity and regime health score")
    risk_multiplier: float = Field(ge=0.0, le=1.0, description="LLM risk multiplier (can only tighten risk)")
    confidence: float = Field(ge=0.0, le=1.0, description="Model confidence in macro assessment")
    timestamp_ns: int = Field(description="UNIX timestamp in nanoseconds")
```

---

## 4. Tick-Level Simulator & Validation Framework

### 4.1 Queue Position & Matching Realism
- **FIFO Limit Order Book Queue**:
  Orders added at a price level sit behind existing volume $V_{\text{ahead}}$.
- **Queue Degradation on Cancels**:
  When external cancellations occur at a price level, order priority degrades proportionally:
  $$\Delta V_{\text{ahead}} = \Delta V_{\text{cancel}} \cdot \frac{V_{\text{ahead}}}{V_{\text{ahead}} + V_{\text{behind}}}$$
- **Partial Fills**:
  Incoming market trades fill queue front volume first before filling the strategy's orders.
- **Two-Way Latency Modeling**:
  Order placement, cancellation, and execution acknowledge delays are sampled from empirical lognormal distributions:
  $$\tau_{\text{ack}} \sim \text{Lognormal}(\mu_{\text{lat}}, \sigma_{\text{lat}}^2)$$
- **2x Latency Stress Test**:
  All validation suites run with $2\times$ measured latency to guarantee robustness under network degradation.

### 4.2 Purged & Embargoed Walk-Forward Validation
- Follows Marcos Lopez de Prado's combinatorial purged cross-validation (CPCV) to eliminate look-ahead leakage and autocorrelation contamination.
- Comprehensive markout reports generated at $t + 100\text{ms}, t + 1\text{s}, t + 5\text{s}, t + 30\text{s}$.

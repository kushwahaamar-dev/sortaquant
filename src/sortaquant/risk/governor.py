"""Deterministic Risk Governor.

Enforces absolute hard limits outside and above all models and LLM recommendations.
The LLM may recommend tighter risk (m < 1.0) but can NEVER loosen hard stops or increase exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from sortaquant.core.types import QuotePair, Fill, Side
from sortaquant.risk.limits import RiskLimitsConfig, DynamicInventoryLimit
from sortaquant.risk.watchdog import StaleFeedWatchdog
from sortaquant.risk.rate_limiter import TokenBucketRateLimiter


@dataclass(frozen=True)
class GovernedQuotes:
    bid_price: Optional[float]
    bid_size: float
    ask_price: Optional[float]
    ask_size: float
    is_halted: bool
    q_max: float
    applied_risk_multiplier: float
    reasons: List[str] = field(default_factory=list)


class DeterministicRiskGovernor:
    """The supreme risk authority in the RAIM system."""

    def __init__(self, config: Optional[RiskLimitsConfig] = None):
        self.config = config or RiskLimitsConfig()
        self.inventory_limiter = DynamicInventoryLimit(self.config)
        self.watchdog = StaleFeedWatchdog(timeout_ns=self.config.stale_feed_timeout_ns)
        self.rate_limiter = TokenBucketRateLimiter(
            max_tokens=self.config.max_messages_per_second,
            refill_rate_per_sec=self.config.max_messages_per_second,
        )

        # Internal state tracking
        self.current_position: float = 0.0
        self.realized_pnl: float = 0.0
        self.high_water_mark: float = self.config.capital_budget
        self.consecutive_loss_streak: int = 0
        self.is_drawdown_halted: bool = False
        self.is_streak_paused: bool = False
        self.streak_pause_until_ns: int = 0

    def on_feed_packet(self, timestamp_ns: int) -> None:
        self.watchdog.on_packet(timestamp_ns)

    def on_fill(self, fill: Fill, current_mid: float) -> None:
        """Processes execution fill and updates inventory and PnL."""
        signed_size = fill.size if fill.side == Side.BUY else -fill.size
        self.current_position += signed_size

        # Update cash/realized PnL
        fill_cash = fill.price * fill.size
        fee_cash = fill.fee
        if fill.side == Side.BUY:
            self.realized_pnl -= fee_cash
        else:
            self.realized_pnl -= fee_cash

        # Markout check
        # If fill direction resulted in negative immediate drift
        is_loss = False
        if fill.side == Side.BUY and current_mid < fill.price:
            is_loss = True
        elif fill.side == Side.SELL and current_mid > fill.price:
            is_loss = True

        if is_loss:
            self.consecutive_loss_streak += 1
            if self.consecutive_loss_streak >= self.config.max_loss_streak:
                self.is_streak_paused = True
                self.streak_pause_until_ns = fill.timestamp_ns + 30_000_000_000  # 30s pause
        else:
            self.consecutive_loss_streak = 0
            self.is_streak_paused = False

    def update_pnl_and_drawdown(self, current_mid: float) -> float:
        """Calculates total equity = Capital + Realized PnL + Unrealized PnL."""
        unrealized = self.current_position * current_mid
        total_equity = self.config.capital_budget + self.realized_pnl + unrealized
        self.high_water_mark = max(self.high_water_mark, total_equity)

        drawdown = (self.high_water_mark - total_equity) / self.high_water_mark
        if drawdown >= self.config.max_drawdown_pct:
            self.is_drawdown_halted = True

        return total_equity

    def evaluate_quotes(
        self,
        quotes: QuotePair,
        mid_price: float,
        volatility: float,
        l2_depth: float,
        now_ns: int,
        llm_risk_multiplier: float = 1.0,
    ) -> GovernedQuotes:
        """Applies hard deterministic risk governance to proposed quotes."""
        reasons: List[str] = []

        # 1. Stale-feed watchdog check
        if not self.watchdog.check(now_ns):
            reasons.append("HALT: Stale feed timeout exceeded (no WebSocket packet in 500ms)")
            return GovernedQuotes(
                bid_price=None,
                bid_size=0.0,
                ask_price=None,
                ask_size=0.0,
                is_halted=True,
                q_max=0.0,
                applied_risk_multiplier=0.0,
                reasons=reasons,
            )

        # 2. Drawdown circuit breaker
        self.update_pnl_and_drawdown(mid_price)
        if self.is_drawdown_halted:
            reasons.append(f"HALT: High-water mark drawdown >= {self.config.max_drawdown_pct*100:.1f}%")
            return GovernedQuotes(
                bid_price=None,
                bid_size=0.0,
                ask_price=None,
                ask_size=0.0,
                is_halted=True,
                q_max=0.0,
                applied_risk_multiplier=0.0,
                reasons=reasons,
            )

        # 3. Loss streak pause check
        if self.is_streak_paused:
            if now_ns < self.streak_pause_until_ns:
                reasons.append("PAUSE: Adverse markout loss streak circuit breaker active")
                return GovernedQuotes(
                    bid_price=None,
                    bid_size=0.0,
                    ask_price=None,
                    ask_size=0.0,
                    is_halted=True,
                    q_max=0.0,
                    applied_risk_multiplier=0.0,
                    reasons=reasons,
                )
            else:
                self.is_streak_paused = False
                self.consecutive_loss_streak = 0

        # 4. Message rate limiter
        if not self.rate_limiter.can_send(now_ns, cost=1.0):
            reasons.append("THROTTLED: Exchange message token bucket exhausted")
            return GovernedQuotes(
                bid_price=None,
                bid_size=0.0,
                ask_price=None,
                ask_size=0.0,
                is_halted=False,
                q_max=0.0,
                applied_risk_multiplier=0.0,
                reasons=reasons,
            )

        # 5. Asymmetric LLM Rule: m_applied = min(m_LLM, m_rules)
        # Rules base multiplier is 1.0 (or smaller if streak)
        m_rules = 0.5 if self.consecutive_loss_streak > 2 else 1.0
        # Clamped strictly in [0, 1] - LLM can never loosen risk!
        m_llm_clamped = min(1.0, max(0.0, llm_risk_multiplier))
        applied_multiplier = min(m_rules, m_llm_clamped)

        # 6. Dynamic inventory ceiling Q_max
        q_max = self.inventory_limiter.compute_q_max(
            mid_price=mid_price,
            volatility=volatility,
            l2_depth_volume=l2_depth,
        )

        bid_price = quotes.bid_price
        ask_price = quotes.ask_price
        bid_size = quotes.bid_size * applied_multiplier
        ask_size = quotes.ask_size * applied_multiplier

        # Hard Position Clamping against Q_max:
        # Long position clamping
        if self.current_position >= q_max:
            bid_price = None
            bid_size = 0.0
            reasons.append(f"CLAMP: Inventory {self.current_position:.2f} >= Q_max {q_max:.2f}. Bids cancelled.")
        elif self.current_position + bid_size > q_max:
            bid_size = max(0.0, q_max - self.current_position)
            reasons.append(f"CLAMP: Bid size reduced to respect Q_max {q_max:.2f}.")

        # Short position clamping
        if self.current_position <= -q_max:
            ask_price = None
            ask_size = 0.0
            reasons.append(f"CLAMP: Inventory {self.current_position:.2f} <= -Q_max {q_max:.2f}. Asks cancelled.")
        elif self.current_position - ask_size < -q_max:
            ask_size = max(0.0, q_max + self.current_position)
            reasons.append(f"CLAMP: Ask size reduced to respect -Q_max {-q_max:.2f}.")

        # Consume token from rate limiter
        self.rate_limiter.consume(now_ns, cost=1.0)

        return GovernedQuotes(
            bid_price=bid_price,
            bid_size=bid_size,
            ask_price=ask_price,
            ask_size=ask_size,
            is_halted=False,
            q_max=q_max,
            applied_risk_multiplier=applied_multiplier,
            reasons=reasons,
        )

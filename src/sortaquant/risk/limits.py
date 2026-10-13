"""Deterministic Risk Limits and Dynamic Inventory Ceiling."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimitsConfig:
    capital_budget: float = 100_000.0       # B (USD)
    z_score: float = 2.58                   # 99% VaR confidence level
    h_unwind_s: float = 30.0                # Unwind horizon in seconds
    depth_fraction: float = 0.05            # c parameter (fraction of book depth)
    max_gross_notional: float = 250_000.0   # Hard ceiling on gross position
    max_drawdown_pct: float = 0.02          # 2.0% High-water mark drawdown halt
    max_loss_streak: int = 4                # Consecutive markout losses before pause
    stale_feed_timeout_ns: int = 500_000_000  # 500 ms heartbeat timeout
    max_messages_per_second: float = 20.0   # Exchange rate limit


class DynamicInventoryLimit:
    """Computes Q_max = min(B / (z * sigma * sqrt(h_unwind)), c * depth)."""

    def __init__(self, config: RiskLimitsConfig):
        self.config = config

    def compute_q_max(self, mid_price: float, volatility: float, l2_depth_volume: float) -> float:
        """Computes maximum allowed inventory in base asset units."""
        if mid_price <= 0:
            return 0.0

        # Annualized or per-second volatility scaled to unwind horizon
        sigma = max(1e-4, volatility)
        h = max(1.0, self.config.h_unwind_s)

        # VaR-based capital capacity limit in notional USD
        # Var_limit = B / (z * sigma * sqrt(h))
        # In base units: (B / mid_price) / (z * sigma * sqrt(h))
        var_notional = self.config.capital_budget / (self.config.z_score * sigma * math.sqrt(h))
        q_var = var_notional / mid_price

        # Liquidity depth limit: c * depth
        q_depth = self.config.depth_fraction * max(0.1, l2_depth_volume)

        # Gross notional hard cap
        q_gross = self.config.max_gross_notional / mid_price

        q_max = min(q_var, q_depth, q_gross)
        return float(max(0.001, q_max))

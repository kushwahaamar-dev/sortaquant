"""Guéant-Lehalle-Fernandez-Tapia (2013) & Avellaneda-Stoikov (2008) Optimal Quoting Models.

Implements inventory-aware reservation price and closed-form optimal half-spreads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class OptimalQuotes:
    fair_value: float
    reservation_price: float
    optimal_bid: float
    optimal_ask: float
    bid_half_spread: float
    ask_half_spread: float
    inventory: float


class GueantLehalleModel:
    """Closed-form Guéant-Lehalle-Fernandez-Tapia (2013) optimal inventory market making model."""

    def __init__(
        self,
        base_gamma: float = 0.1,  # Risk aversion
        k: float = 1.5,           # Fill probability decay intensity
        time_horizon_s: float = 60.0,
    ):
        self.base_gamma = base_gamma
        self.k = k
        self.time_horizon_s = time_horizon_s

    def compute_reservation_price(
        self,
        fair_value: float,
        inventory: float,
        volatility: float,
        gamma: float,
    ) -> float:
        """Avellaneda-Stoikov reservation price: r(v, q) = v - q * gamma * sigma^2 * (T - t)."""
        dt = max(1.0, self.time_horizon_s) / 60.0
        inventory_penalty = inventory * gamma * (volatility**2) * dt
        return fair_value - inventory_penalty

    def compute_half_spreads(
        self,
        inventory: float,
        volatility: float,
        gamma: float,
        min_half_spread: float = 0.01,
    ) -> Tuple[float, float]:
        """Computes delta_a(q) and delta_b(q) under Guéant-Lehalle (2013):

        delta^a(q) = (1/gamma) * ln(1 + gamma/k) + ((2q + 1) * gamma * sigma^2) / (2 * k)
        delta^b(q) = (1/gamma) * ln(1 + gamma/k) - ((2q - 1) * gamma * sigma^2) / (2 * k)
        """
        g = max(1e-4, gamma)
        k = max(1e-4, self.k)
        vol_term = (g * (volatility**2)) / (2.0 * k)

        base_spread = (1.0 / g) * math.log(1.0 + (g / k))

        # When inventory q > 0 (long):
        # delta_ask decreases (ask drops closer to mid to encourage fills)
        # delta_bid increases (bid drops further from mid to discourage fills)
        delta_ask = base_spread - (2.0 * inventory - 1.0) * vol_term
        delta_bid = base_spread + (2.0 * inventory + 1.0) * vol_term

        # Clamp half spreads to enforce non-negative sensible margins
        delta_ask = max(min_half_spread, delta_ask)
        delta_bid = max(min_half_spread, delta_bid)

        return delta_bid, delta_ask

    def compute_quotes(
        self,
        fair_value: float,
        inventory: float,
        volatility: float,
        gamma: float,
        min_half_spread: float = 0.01,
    ) -> OptimalQuotes:
        """Computes full optimal quote pair."""
        r_price = self.compute_reservation_price(fair_value, inventory, volatility, gamma)
        delta_bid, delta_ask = self.compute_half_spreads(inventory, volatility, gamma, min_half_spread)

        optimal_bid = fair_value - delta_bid
        optimal_ask = fair_value + delta_ask

        return OptimalQuotes(
            fair_value=fair_value,
            reservation_price=r_price,
            optimal_bid=optimal_bid,
            optimal_ask=optimal_ask,
            bid_half_spread=delta_bid,
            ask_half_spread=delta_ask,
            inventory=inventory,
        )

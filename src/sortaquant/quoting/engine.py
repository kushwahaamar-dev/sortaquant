"""Unified Regime-Adaptive, Inventory-Aware Quoting Engine."""

from __future__ import annotations

import math
from typing import Optional
from sortaquant.core.types import BBO, QuotePair, RegimeState
from sortaquant.quoting.microprice import FairValueEstimator
from sortaquant.quoting.gueant_lehalle import GueantLehalleModel
from sortaquant.regime.engine import RegimeOutput


class QuotingEngine:
    """Computes dynamic bid/ask quotes adapted to inventory and market regime."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        base_gamma: float = 0.15,
        k: float = 1.2,
        base_quote_size: float = 0.10,
        tick_size: float = 0.01,
        lot_size: float = 0.001,
        beta_ofi: float = 0.04,
    ):
        self.symbol = symbol
        self.base_gamma = base_gamma
        self.k = k
        self.base_quote_size = base_quote_size
        self.tick_size = tick_size
        self.lot_size = lot_size

        self.fair_value_estimator = FairValueEstimator(beta_ofi=beta_ofi)
        self.model = GueantLehalleModel(base_gamma=base_gamma, k=k)

    def round_price(self, price: float, is_bid: bool) -> float:
        """Rounds bids down and asks up to enforce maker status and prevent crosses."""
        ticks = price / self.tick_size
        if is_bid:
            return round(math.floor(ticks) * self.tick_size, 8)
        else:
            return round(math.ceil(ticks) * self.tick_size, 8)

    def round_size(self, size: float) -> float:
        lots = math.floor(size / self.lot_size)
        return round(lots * self.lot_size, 8)

    def generate_quotes(
        self,
        bbo: BBO,
        inventory: float,
        volatility: float,
        regime_output: RegimeOutput,
        ofi_value: float = 0.0,
        risk_multiplier: float = 1.0,
    ) -> QuotePair:
        """Generates dynamic two-sided quotes adapted to regime and inventory."""
        # 1. Check if regime engine enforces complete quote pull
        if regime_output.is_halted or regime_output.active_regime == RegimeState.R4_BREAK:
            return QuotePair(
                bid_price=None,
                bid_size=0.0,
                ask_price=None,
                ask_size=0.0,
                fair_value=bbo.mid_price,
                regime=RegimeState.R4_BREAK,
                timestamp_ns=bbo.timestamp_ns,
                metadata={"status": "HALTED", "reason": regime_output.reason},
            )

        # 2. Fair value with microprice and OFI drift
        fair_value = self.fair_value_estimator.estimate_fair_value(bbo, ofi_value=ofi_value)

        # 3. Dynamic risk aversion scaled by regime and risk multiplier
        effective_gamma = self.base_gamma * regime_output.blended_risk_aversion_mult

        # 4. Compute optimal base quotes
        min_half = max(self.tick_size, bbo.spread * 0.4) * regime_output.blended_spread_mult
        opt = self.model.compute_quotes(
            fair_value=fair_value,
            inventory=inventory,
            volatility=max(0.0001, volatility),
            gamma=effective_gamma,
            min_half_spread=min_half,
        )

        raw_bid = opt.optimal_bid
        raw_ask = opt.optimal_ask

        # 5. Regime-specific quote adjustments
        # In R2 (Trending / Informed): Asymmetric quote skewing
        if regime_output.active_regime == RegimeState.R2_TRENDING:
            # If positive OFI (upward flow): fade ask (widen ask) to avoid toxic fills
            if ofi_value > 0:
                raw_ask += bbo.spread * 0.5
            elif ofi_value < 0:
                raw_bid -= bbo.spread * 0.5

        # 6. Discrete tick rounding
        bid_price = self.round_price(raw_bid, is_bid=True)
        ask_price = self.round_price(raw_ask, is_bid=False)

        # Safety: bid must be strictly less than ask
        if bid_price >= ask_price:
            bid_price = ask_price - self.tick_size

        # Safety: avoid placing marketable orders (must not cross BBO)
        bid_price = min(bid_price, bbo.bid_price)
        ask_price = max(ask_price, bbo.ask_price)

        # 7. Quote sizes adapted to regime, inventory, and risk multiplier
        base_size = self.base_quote_size * regime_output.blended_size_mult * risk_multiplier

        # Inventory damping on size: if long, reduce bid size and increase ask size
        bid_size_scale = max(0.1, 1.0 - inventory * 0.2)
        ask_size_scale = max(0.1, 1.0 + inventory * 0.2)

        bid_size = self.round_size(base_size * bid_size_scale)
        ask_size = self.round_size(base_size * ask_size_scale)

        return QuotePair(
            bid_price=bid_price,
            bid_size=bid_size,
            ask_price=ask_price,
            ask_size=ask_size,
            fair_value=fair_value,
            regime=regime_output.active_regime,
            timestamp_ns=bbo.timestamp_ns,
            metadata={
                "reservation_price": opt.reservation_price,
                "effective_gamma": effective_gamma,
                "spread_mult": regime_output.blended_spread_mult,
                "size_mult": regime_output.blended_size_mult,
            },
        )

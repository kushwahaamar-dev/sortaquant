"""Stoikov (2018) Micro-price and OFI-adjusted Fair Value Estimator."""

from __future__ import annotations

from sortaquant.core.types import BBO


class FairValueEstimator:
    """Estimates high-frequency fair value by combining order book micro-price and short-horizon OFI drift."""

    def __init__(self, beta_ofi: float = 0.05):
        self.beta_ofi = beta_ofi

    def compute_micro_price(self, bbo: BBO) -> float:
        """Stoikov (2018) micro-price: M + ((V_b - V_a)/(V_b + V_a)) * (Spread / 2)."""
        tot_vol = bbo.bid_size + bbo.ask_size
        if tot_vol <= 1e-12:
            return bbo.mid_price
        imbalance = (bbo.bid_size - bbo.ask_size) / tot_vol
        return bbo.mid_price + imbalance * (bbo.spread * 0.5)

    def estimate_fair_value(self, bbo: BBO, ofi_value: float = 0.0) -> float:
        """Computes v_hat = MicroPrice + beta_OFI * OFI."""
        micro_price = self.compute_micro_price(bbo)
        # Shift in ticks/currency units proportional to OFI
        drift = self.beta_ofi * ofi_value * (bbo.spread if bbo.spread > 0 else 0.01)
        return micro_price + drift

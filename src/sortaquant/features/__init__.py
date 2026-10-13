"""Microstructure feature extraction modules for RAIM engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import numpy as np

from sortaquant.core.types import BBO, BookLevel, Trade
from sortaquant.features.bipower import BipowerVariationEstimator
from sortaquant.features.ofi import OFIEstimator
from sortaquant.features.vpin import VPINEstimator
from sortaquant.features.variance_ratio import VarianceRatioEstimator
from sortaquant.features.hawkes import HawkesIntensityEstimator


@dataclass(frozen=True)
class MicrostructureFeatures:
    timestamp_ns: int
    mid_price: float
    spread_bps: float
    book_imbalance: float
    bipower_vol: float
    realized_vol: float
    jump_ratio: float
    ofi: float
    ofi_zscore: float
    vpin: float
    variance_ratio: float
    hawkes_n: float

    def to_vector(self) -> np.ndarray:
        """Returns normalized 7-dimensional observation vector for HMM."""
        return np.array(
            [
                self.bipower_vol * 100.0,
                self.spread_bps,
                self.book_imbalance,
                self.vpin,
                self.ofi_zscore,
                self.variance_ratio - 1.0,  # 0 indicates random walk
                self.hawkes_n,
            ],
            dtype=np.float64,
        )


class MicrostructureFeatureEngine:
    """Aggregates real-time order book and trade streams into stationary microstructure features."""

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.bipower = BipowerVariationEstimator(window_size=120)
        self.ofi = OFIEstimator(window_size=60)
        self.vpin = VPINEstimator(bucket_size=5.0, num_buckets=30)
        self.variance_ratio = VarianceRatioEstimator(window_size=120)
        self.hawkes = HawkesIntensityEstimator(window_size=100)
        self._last_features: Optional[MicrostructureFeatures] = None

    def on_bbo(self, bbo: BBO, bids: Optional[List[BookLevel]] = None, asks: Optional[List[BookLevel]] = None) -> MicrostructureFeatures:
        mid = bbo.mid_price
        self.bipower.update(mid)
        self.variance_ratio.update(mid)

        ofi_val = self.ofi.update_bbo(bbo)

        # Imbalance
        tot_vol = bbo.bid_size + bbo.ask_size
        imbalance = (bbo.bid_size - bbo.ask_size) / tot_vol if tot_vol > 0 else 0.0

        bv_vol, rv_vol, jump_ratio = self.bipower.compute()
        vr = self.variance_ratio.compute(horizon=5)

        features = MicrostructureFeatures(
            timestamp_ns=bbo.timestamp_ns,
            mid_price=mid,
            spread_bps=bbo.relative_spread_bps,
            book_imbalance=imbalance,
            bipower_vol=bv_vol,
            realized_vol=rv_vol,
            jump_ratio=jump_ratio,
            ofi=ofi_val,
            ofi_zscore=self.ofi.ofi_zscore,
            vpin=self.vpin.vpin,
            variance_ratio=vr,
            hawkes_n=self.hawkes.branching_ratio,
        )
        self._last_features = features
        return features

    def on_trade(self, trade: Trade) -> None:
        self.vpin.add_trade(trade)
        self.hawkes.update(trade.timestamp_ns)

    @property
    def latest_features(self) -> Optional[MicrostructureFeatures]:
        return self._last_features

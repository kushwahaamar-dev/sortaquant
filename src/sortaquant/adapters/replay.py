"""Market Data Replay and Synthetic Regime-Switching Generator."""

from __future__ import annotations

import math
from typing import Iterator, List, Tuple
import numpy as np

from sortaquant.core.types import BBO, Side, Trade
from sortaquant.adapters.base import BaseExchangeAdapter


class MarketReplayAdapter(BaseExchangeAdapter):
    """Replays deterministic tick streams with realistic regime transitions."""

    def __init__(self, symbol: str = "BTCUSDT"):
        super().__init__(symbol)
        self._is_active = False

    async def connect(self) -> None:
        self._is_active = True

    async def disconnect(self) -> None:
        self._is_active = False

    @classmethod
    def generate_synthetic_regime_dataset(
        cls,
        n_ticks: int = 500,
        start_price: float = 60_000.0,
        seed: int = 42,
    ) -> Tuple[List[BBO], List[Trade]]:
        """Generates realistic market replay data transitioning through all 4 regimes.

        Ticks:
        0 - 150: R1 (Calm, Mean-Reverting)
        151 - 280: R2 (Trending / Informed momentum)
        281 - 400: R3 (High-Vol / Stress)
        401 - 460: R4 (Structural Break / Flash Crash)
        461 - 500: Recovery to R1
        """
        rng = np.random.default_rng(seed)
        bbos: List[BBO] = []
        trades: List[Trade] = []

        price = start_price
        base_t_ns = 1_700_000_000_000_000_000  # Nanoseconds

        for i in range(n_ticks):
            t_ns = base_t_ns + i * 100_000_000  # 100ms per tick

            # Determine regime characteristics
            if i < 150:
                # R1: Calm, tight spread ~1 bps, mean-reversion
                spread = price * 0.0001
                drift = -0.1 * (price - start_price) + rng.normal(0, 0.5)
                vol = 0.5
                bid_sz = rng.uniform(5.0, 10.0)
                ask_sz = rng.uniform(5.0, 10.0)
            elif i < 280:
                # R2: Trending upward, steady drift, buyer aggression
                spread = price * 0.00015
                drift = 2.5 + rng.normal(0, 1.0)
                vol = 1.0
                bid_sz = rng.uniform(8.0, 15.0)  # Heavy bids
                ask_sz = rng.uniform(2.0, 5.0)
            elif i < 400:
                # R3: High vol, widening spread ~4 bps, volatile swings
                spread = price * 0.0004
                drift = rng.normal(0, 12.0)
                vol = 8.0
                bid_sz = rng.uniform(2.0, 6.0)
                ask_sz = rng.uniform(2.0, 6.0)
            elif i < 460:
                # R4: Structural Break / Flash Crash: massive downward cascade, spread blown out ~10 bps
                spread = price * 0.0010
                drift = -25.0 + rng.normal(0, 5.0)
                vol = 20.0
                bid_sz = rng.uniform(0.5, 2.0)  # Bids vaporized
                ask_sz = rng.uniform(15.0, 30.0)
            else:
                # Recovery back to R1
                spread = price * 0.00012
                drift = rng.normal(0, 0.8)
                vol = 0.6
                bid_sz = rng.uniform(6.0, 12.0)
                ask_sz = rng.uniform(6.0, 12.0)

            price = max(100.0, price + drift)
            half_s = spread * 0.5
            bid_p = round(price - half_s, 2)
            ask_p = round(price + half_s, 2)

            bbo = BBO(
                bid_price=bid_p,
                bid_size=bid_sz,
                ask_price=ask_p,
                ask_size=ask_sz,
                timestamp_ns=t_ns,
            )
            bbos.append(bbo)

            # Generate trade matching regime flow
            if i % 2 == 0:
                trade_side = Side.BUY if (drift > 0 or rng.random() > 0.6) else Side.SELL
                trade_p = ask_p if trade_side == Side.BUY else bid_p
                trade_sz = rng.uniform(1.0, 4.0)
                trades.append(
                    Trade(
                        price=trade_p,
                        size=trade_sz,
                        side=trade_side,
                        timestamp_ns=t_ns + 10_000_000,
                        trade_id=f"t_{i}",
                    )
                )

        return bbos, trades

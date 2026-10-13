"""Core types and domain models for RAIM market making engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Tuple


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self == Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self == Side.BUY else Side.BUY


class OrderType(Enum):
    LIMIT = "LIMIT"
    POST_ONLY = "POST_ONLY"
    MARKET = "MARKET"


class OrderStatus(Enum):
    PENDING_NEW = "PENDING_NEW"
    OPEN = "OPEN"
    PENDING_CANCEL = "PENDING_CANCEL"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"


class RegimeState(Enum):
    R1_CALM = 0
    R2_TRENDING = 1
    R3_HIGHVOL = 2
    R4_BREAK = 3

    @property
    def label(self) -> str:
        names = {
            RegimeState.R1_CALM: "Calm / Mean-Reverting",
            RegimeState.R2_TRENDING: "Trending / Informed",
            RegimeState.R3_HIGHVOL: "High-Vol / Stress",
            RegimeState.R4_BREAK: "Structural Break / Flash Crash",
        }
        return names[self]


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class BBO:
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    timestamp_ns: int

    @property
    def mid_price(self) -> float:
        return 0.5 * (self.bid_price + self.ask_price)

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    @property
    def relative_spread_bps(self) -> float:
        mid = self.mid_price
        if mid <= 0:
            return 0.0
        return (self.spread / mid) * 10_000.0

    @property
    def micro_price(self) -> float:
        tot = self.bid_size + self.ask_size
        if tot <= 0:
            return self.mid_price
        # Microprice = Bid + AskSize / (BidSize + AskSize) * Spread
        # Or Mid + ((BidSize - AskSize) / (2 * (BidSize + AskSize))) * Spread
        return self.bid_price + (self.bid_size / tot) * self.spread


@dataclass
class Trade:
    price: float
    size: float
    side: Side
    timestamp_ns: int
    trade_id: str = ""
    is_buyer_maker: bool = False


@dataclass
class Order:
    order_id: str
    symbol: str
    side: Side
    price: float
    size: float
    order_type: OrderType = OrderType.POST_ONLY
    status: OrderStatus = OrderStatus.PENDING_NEW
    filled_size: float = 0.0
    remaining_size: float = 0.0
    created_at_ns: int = 0
    updated_at_ns: int = 0

    def __post_init__(self):
        if self.remaining_size == 0.0 and self.filled_size == 0.0:
            self.remaining_size = self.size


@dataclass
class QuotePair:
    bid_price: Optional[float]
    bid_size: float
    ask_price: Optional[float]
    ask_size: float
    fair_value: float
    regime: RegimeState
    timestamp_ns: int
    metadata: dict = field(default_factory=dict)


@dataclass
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    price: float
    size: float
    fee: float
    is_maker: bool
    timestamp_ns: int
    mid_at_fill: float

"""Abstract Base Exchange Adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional
from sortaquant.core.types import BBO, Trade


class BaseExchangeAdapter(ABC):
    """Abstract interface for exchange market data and order management."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.on_bbo: Optional[Callable[[BBO], None]] = None
        self.on_trade: Optional[Callable[[Trade], None]] = None

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

"""Contrato de persistencia de datos de mercado."""

from collections.abc import Sequence
from typing import Protocol

from app.market.models import Candle, Trade


class MarketStore(Protocol):
    """Sumidero de persistencia para ticks y velas (implementación libre)."""

    def add_trade(self, trade: Trade) -> None:
        """Buffer one trade for persistence (must never block)."""
        ...

    def add_candles(self, candles: Sequence[Candle]) -> None:
        """Buffer closed candles for persistence (must never block)."""
        ...

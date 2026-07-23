"""Proveedores de datos de mercado (adaptadores por exchange/broker)."""

from app.market.providers.base import BaseWSProvider
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider
from app.market.providers.prepared import (
    BitgetProvider,
    IBKRProvider,
    MT5Provider,
    OandaProvider,
    PreparedProvider,
)
from app.market.providers.registry import ProviderRegistry

__all__ = [
    "BaseWSProvider",
    "BinanceProvider",
    "BitgetProvider",
    "BybitProvider",
    "IBKRProvider",
    "MT5Provider",
    "OKXProvider",
    "OandaProvider",
    "PreparedProvider",
    "ProviderRegistry",
]

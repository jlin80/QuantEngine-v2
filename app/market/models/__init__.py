"""Modelos internos del Data Engine (contratos tipados e inmutables)."""

from app.market.models.enums import (
    ChannelType,
    ConnectionState,
    QualityIssueType,
    TradeSide,
)
from app.market.models.market import (
    OHLCV,
    Candle,
    DepthLevel,
    FundingRate,
    Liquidation,
    MarketSnapshot,
    MarketState,
    OpenInterest,
    OrderBook,
    OrderBookDelta,
    Ticker,
    Trade,
    latency_ms,
)
from app.market.models.timeframe import Timeframe

MarketObject = Ticker | Trade | Candle | OrderBookDelta | FundingRate | OpenInterest | Liquidation
"""Unión de todo objeto normalizado que un proveedor puede emitir."""

__all__ = [
    "OHLCV",
    "Candle",
    "ChannelType",
    "ConnectionState",
    "DepthLevel",
    "FundingRate",
    "Liquidation",
    "MarketObject",
    "MarketSnapshot",
    "MarketState",
    "OpenInterest",
    "OrderBook",
    "OrderBookDelta",
    "QualityIssueType",
    "Ticker",
    "Timeframe",
    "Trade",
    "TradeSide",
    "latency_ms",
]

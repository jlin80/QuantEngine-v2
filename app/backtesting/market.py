"""Mercado histórico reproducido para el laboratorio (Fase 6).

Construye un :class:`~app.market.services.MarketDataService` real respaldado por
un :class:`~app.market.services.state.MarketStateStore` en memoria, poblado a
medida que avanza el reloj de replay. El Execution Engine lo consume igual que
al servicio de datos en vivo — no distingue entre backtest y paper real. Esta
es la clave de "no mantener dos motores distintos".
"""

from datetime import datetime

from app.cache import CacheService, InMemoryCache
from app.market.cache import MarketCache
from app.market.models import Candle, Ticker
from app.market.services import MarketDataService
from app.market.services.state import MarketStateStore


class HistoricalMarket:
    """In-memory market data replayed bar by bar for a backtest.

    Args:
        provider: Etiqueta de proveedor de los tickers sintéticos.
        candle_history_limit: Velas retenidas por (símbolo, timeframe).
    """

    def __init__(self, *, provider: str = "backtest", candle_history_limit: int = 10_000) -> None:
        self._provider = provider
        self._state = MarketStateStore(candle_history_limit=candle_history_limit)
        cache_service = CacheService(primary=None, fallback=InMemoryCache())
        self._service = MarketDataService(self._state, MarketCache(cache_service))

    @property
    def service(self) -> MarketDataService:
        """The broker-agnostic market data service the engine reads from."""
        return self._service

    def push_candle(self, candle: Candle) -> None:
        """Store a closed candle so ATR and strategies can see it."""
        self._state.update_candle(candle)

    def push_price(
        self, symbol: str, price: float, *, timestamp: datetime, spread_bps: float
    ) -> None:
        """Publish a ticker at ``price`` with a synthetic spread.

        Args:
            symbol: Símbolo interno.
            price: Precio medio (mid) del tick.
            timestamp: Momento del tick (UTC simulado).
            spread_bps: Spread aplicado alrededor del mid, en bps.
        """
        half = price * (spread_bps / 2.0) / 10_000.0
        self._state.update_ticker(
            Ticker(
                symbol=symbol.upper(),
                provider=self._provider,
                bid=round(price - half, 8),
                ask=round(price + half, 8),
                last=round(price, 8),
                exchange_ts=timestamp,
                local_ts=timestamp,
            )
        )

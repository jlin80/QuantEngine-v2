"""MarketDataService: la API interna de datos, independiente del exchange.

Este es el único punto por el que el resto del sistema (estrategias, riesgo,
dashboard) consume datos de mercado. Nada de aquí revela qué broker sirvió
cada dato.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.market.cache import MarketCache
from app.market.models import (
    Candle,
    FundingRate,
    MarketSnapshot,
    MarketState,
    OpenInterest,
    OrderBook,
    Ticker,
    Timeframe,
    Trade,
)
from app.market.services.state import MarketStateStore
from app.utils.time import utc_now

if TYPE_CHECKING:  # evita el ciclo feed → collector → services → feed
    from app.market.feed import MarketFeed


class MarketDataService:
    """Broker-agnostic read API over the Data Engine.

    Args:
        state: Estado vivo en memoria (fuente primaria).
        cache: Cache de mercado (fuente secundaria, sobrevive reinicios).
        feed: Feed para suscripciones y estado de conexión.
    """

    def __init__(
        self, state: MarketStateStore, cache: MarketCache, feed: "MarketFeed | None" = None
    ) -> None:
        self._state = state
        self._cache = cache
        self._feed = feed

    # ------------------------------------------------------------------
    # Suscripción (sin conocer el broker)
    # ------------------------------------------------------------------

    async def subscribe(self, symbol: str) -> None:
        """Subscribe a symbol using the configured routing.

        Raises:
            ProviderError: Si el proveedor ruteado no está disponible.
        """
        if self._feed is not None:
            await self._feed.subscribe(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        """Drop the subscription for a symbol."""
        if self._feed is not None:
            await self._feed.unsubscribe(symbol)

    # ------------------------------------------------------------------
    # Lecturas puntuales
    # ------------------------------------------------------------------

    async def get_last_price(self, symbol: str) -> float | None:
        """Last traded price (fallback: mid del ticker, luego cache)."""
        trade = self._state.last_trade(symbol)
        if trade is not None:
            return trade.price
        ticker = self._state.ticker(symbol)
        if ticker is not None:
            return ticker.mid
        return await self._cache.get_last_price(symbol)

    def get_ticker(self, symbol: str) -> Ticker | None:
        """Latest best bid/ask."""
        return self._state.ticker(symbol)

    def get_spread(self, symbol: str) -> float | None:
        """Current spread (ticker primero, libro como respaldo)."""
        ticker = self._state.ticker(symbol)
        if ticker is not None:
            return ticker.spread
        book = self._state.book(symbol)
        return book.spread if book is not None else None

    def get_orderbook(self, symbol: str) -> OrderBook | None:
        """Latest order-book snapshot."""
        return self._state.book(symbol)

    def get_depth(self, symbol: str, levels: int = 10) -> tuple[float, float] | None:
        """Visible size per side (``None`` sin libro)."""
        book = self._state.book(symbol)
        return book.depth(levels) if book is not None else None

    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Candle | None:
        """Latest closed candle for a series."""
        return self._state.latest_candle(symbol, timeframe)

    def get_candles(self, symbol: str, timeframe: Timeframe, limit: int = 100) -> list[Candle]:
        """Closed candles, oldest first."""
        return self._state.candles(symbol, timeframe, limit)

    def get_recent_trades(self, symbol: str, limit: int = 100) -> list[Trade]:
        """Most recent trades, oldest first."""
        return self._state.recent_trades(symbol, limit)

    def get_funding_rate(self, symbol: str) -> FundingRate | None:
        """Latest funding rate."""
        return self._state.funding(symbol)

    def get_open_interest(self, symbol: str) -> OpenInterest | None:
        """Latest open interest."""
        return self._state.open_interest(symbol)

    # ------------------------------------------------------------------
    # Compuestos
    # ------------------------------------------------------------------

    def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        """Composite snapshot of everything known about a symbol."""
        symbol = symbol.upper()
        provider = self._feed.provider_of(symbol) if self._feed else None
        candles: dict[str, Candle] = {}
        for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1):
            candle = self._state.latest_candle(symbol, timeframe)
            if candle is not None:
                candles[timeframe.value] = candle
        return MarketSnapshot(
            symbol=symbol,
            provider=provider or "",
            generated_at=utc_now(),
            ticker=self._state.ticker(symbol),
            last_trade=self._state.last_trade(symbol),
            orderbook=self._state.book(symbol),
            candles=candles,
            funding=self._state.funding(symbol),
            open_interest=self._state.open_interest(symbol),
        )

    def get_market_state(self, symbol: str) -> MarketState:
        """Operational state of a symbol (¿el dato está vivo?)."""
        symbol = symbol.upper()
        provider = self._feed.provider_of(symbol) if self._feed else None
        subscribed = provider is not None
        connected = self._feed.is_symbol_connected(symbol) if self._feed else False
        return self._state.market_state(
            symbol,
            provider=provider or "",
            subscribed=subscribed,
            connected=connected,
        )

    @property
    def symbols(self) -> list[str]:
        """Every symbol with data in the engine."""
        return self._state.symbols

    # ------------------------------------------------------------------
    # Streaming interno
    # ------------------------------------------------------------------

    async def get_tick_stream(self, symbol: str | None = None) -> AsyncIterator[Trade]:
        """Async stream of validated trades (``None`` = todos los símbolos).

        Yields:
            Trades a medida que el pipeline los acepta.
        """
        queue = self._state.add_tick_listener(symbol)
        try:
            while True:
                yield await queue.get()
        finally:
            self._state.remove_tick_listener(queue)

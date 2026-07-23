"""Estado vivo del mercado en memoria (último valor de todo, por símbolo).

Es la lectura rápida del Data Engine: el pipeline lo actualiza y el
:class:`~app.market.services.market_data.MarketDataService` lo consulta.
También hace fan-out de trades a los suscriptores de streams internos.
"""

import asyncio
import contextlib
import logging
from collections import deque
from datetime import datetime

from app.market.models import (
    Candle,
    FundingRate,
    MarketState,
    OpenInterest,
    OrderBook,
    Ticker,
    Timeframe,
    Trade,
)
from app.utils.time import utc_now


class MarketStateStore:
    """Last-value cache in process memory + tick fan-out.

    Args:
        recent_trades_limit: Trades recientes retenidos por símbolo.
        candle_history_limit: Velas cerradas retenidas por (símbolo, TF).
    """

    def __init__(self, *, recent_trades_limit: int = 500, candle_history_limit: int = 500) -> None:
        self._trades_limit = recent_trades_limit
        self._candles_limit = candle_history_limit
        self._tickers: dict[str, Ticker] = {}
        self._trades: dict[str, deque[Trade]] = {}
        self._books: dict[str, OrderBook] = {}
        self._candles: dict[tuple[str, Timeframe], deque[Candle]] = {}
        self._funding: dict[str, FundingRate] = {}
        self._open_interest: dict[str, OpenInterest] = {}
        self._tick_counts: dict[str, int] = {}
        self._last_update: dict[str, datetime] = {}
        self._listeners: list[tuple[str | None, asyncio.Queue[Trade]]] = []
        self._log = logging.getLogger("app.market.state")

    # ------------------------------------------------------------------
    # Escritura (pipeline)
    # ------------------------------------------------------------------

    def update_ticker(self, ticker: Ticker) -> None:
        """Store the latest ticker for its symbol."""
        self._tickers[ticker.symbol] = ticker
        self._touch(ticker.symbol, ticker.local_ts)

    def update_trade(self, trade: Trade) -> None:
        """Append a trade, update counters and fan out to listeners."""
        trades = self._trades.get(trade.symbol)
        if trades is None:
            trades = deque(maxlen=self._trades_limit)
            self._trades[trade.symbol] = trades
        trades.append(trade)
        self._tick_counts[trade.symbol] = self._tick_counts.get(trade.symbol, 0) + 1
        self._touch(trade.symbol, trade.local_ts)
        for symbol_filter, queue in self._listeners:
            if symbol_filter is None or symbol_filter == trade.symbol:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(trade)

    def update_book(self, book: OrderBook) -> None:
        """Store the latest order-book snapshot."""
        self._books[book.symbol] = book
        self._touch(book.symbol, book.local_ts)

    def update_candle(self, candle: Candle) -> None:
        """Append a closed candle to its (symbol, timeframe) history."""
        key = (candle.symbol, candle.timeframe)
        candles = self._candles.get(key)
        if candles is None:
            candles = deque(maxlen=self._candles_limit)
            self._candles[key] = candles
        # Las actualizaciones del proveedor pueden repetir la misma vela.
        if candles and candles[-1].start == candle.start:
            candles[-1] = candle
        else:
            candles.append(candle)

    def update_funding(self, funding: FundingRate) -> None:
        """Store the latest funding rate."""
        self._funding[funding.symbol] = funding

    def update_open_interest(self, oi: OpenInterest) -> None:
        """Store the latest open interest."""
        self._open_interest[oi.symbol] = oi

    def _touch(self, symbol: str, moment: datetime) -> None:
        """Record the last data arrival for a symbol."""
        self._last_update[symbol] = moment

    # ------------------------------------------------------------------
    # Lectura (servicio)
    # ------------------------------------------------------------------

    def ticker(self, symbol: str) -> Ticker | None:
        """Latest ticker for a symbol."""
        return self._tickers.get(symbol.upper())

    def last_trade(self, symbol: str) -> Trade | None:
        """Latest trade for a symbol."""
        trades = self._trades.get(symbol.upper())
        return trades[-1] if trades else None

    def recent_trades(self, symbol: str, limit: int = 100) -> list[Trade]:
        """Most recent trades, oldest first."""
        trades = self._trades.get(symbol.upper())
        if not trades:
            return []
        return list(trades)[-limit:]

    def book(self, symbol: str) -> OrderBook | None:
        """Latest order-book snapshot."""
        return self._books.get(symbol.upper())

    def candles(self, symbol: str, timeframe: Timeframe, limit: int = 100) -> list[Candle]:
        """Closed candles, oldest first."""
        series = self._candles.get((symbol.upper(), timeframe))
        if not series:
            return []
        return list(series)[-limit:]

    def latest_candle(self, symbol: str, timeframe: Timeframe) -> Candle | None:
        """Most recent closed candle."""
        series = self._candles.get((symbol.upper(), timeframe))
        return series[-1] if series else None

    def funding(self, symbol: str) -> FundingRate | None:
        """Latest funding rate."""
        return self._funding.get(symbol.upper())

    def open_interest(self, symbol: str) -> OpenInterest | None:
        """Latest open interest."""
        return self._open_interest.get(symbol.upper())

    def tick_count(self, symbol: str) -> int:
        """Accepted trades since startup for a symbol."""
        return self._tick_counts.get(symbol.upper(), 0)

    def last_update(self, symbol: str) -> datetime | None:
        """When the last data for a symbol arrived."""
        return self._last_update.get(symbol.upper())

    def market_state(
        self, symbol: str, *, provider: str, subscribed: bool, connected: bool
    ) -> MarketState:
        """Compose the operational state of a symbol.

        Args:
            symbol: Símbolo interno.
            provider: Proveedor asignado.
            subscribed: Si el feed lo tiene suscrito.
            connected: Si la conexión del proveedor está viva.

        Returns:
            Estado operativo agregado.
        """
        symbol = symbol.upper()
        last = self._last_update.get(symbol)
        staleness = (utc_now() - last).total_seconds() if last is not None else None
        return MarketState(
            symbol=symbol,
            provider=provider,
            subscribed=subscribed,
            connected=connected,
            ticks_received=self._tick_counts.get(symbol, 0),
            last_update=last,
            staleness_seconds=staleness,
        )

    @property
    def symbols(self) -> list[str]:
        """Every symbol with at least one data point."""
        seen = set(self._tickers) | set(self._trades) | set(self._books)
        return sorted(seen)

    # ------------------------------------------------------------------
    # Streams internos
    # ------------------------------------------------------------------

    def add_tick_listener(
        self, symbol: str | None = None, *, max_queue: int = 10_000
    ) -> asyncio.Queue[Trade]:
        """Register an internal tick-stream listener.

        Args:
            symbol: Filtrar por símbolo (``None`` = todos).
            max_queue: Tamaño máximo del buffer del listener.

        Returns:
            Cola de la que el consumidor lee trades.
        """
        queue: asyncio.Queue[Trade] = asyncio.Queue(maxsize=max_queue)
        self._listeners.append((symbol.upper() if symbol else None, queue))
        return queue

    def remove_tick_listener(self, queue: asyncio.Queue[Trade]) -> None:
        """Unregister a tick-stream listener."""
        self._listeners = [(sym, q) for sym, q in self._listeners if q is not queue]

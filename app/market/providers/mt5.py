"""Proveedor de datos MetaTrader 5 (polling local, FX/metales).

MT5 no ofrece WebSocket: este proveedor hace *polling* de ticks con
``symbol_info_tick`` sobre el terminal local (sólo Windows) y emite objetos
:class:`~app.market.models.Ticker` normalizados. Comparte la
:class:`~app.brokers.mt5.connection.MT5Connection` con el broker de ejecución,
así que hay **un único terminal** y un lock común que serializa lecturas
(ticks) y escrituras (``order_send``).

El volumen es órdenes de magnitud menor que el firehose de un exchange cripto,
así que este feed no satura el Event Bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.brokers.mt5.connection import MT5Connection
from app.core.exceptions import ProviderError
from app.market.interfaces.provider import MarketDataProvider
from app.market.models import (
    Candle,
    ChannelType,
    ConnectionState,
    OrderBookDelta,
    Ticker,
    Timeframe,
)
from app.utils.time import utc_now

_log = logging.getLogger("app.market.providers.mt5")

_CAPABILITIES = frozenset({ChannelType.TICKER, ChannelType.TRADES, ChannelType.CANDLES})


def _mt5_timeframe(mt5: Any, timeframe: Timeframe) -> int | None:
    """Map an internal :class:`Timeframe` to an MT5 ``TIMEFRAME_*`` constant."""
    names = {
        Timeframe.M1: "TIMEFRAME_M1",
        Timeframe.M3: "TIMEFRAME_M3",
        Timeframe.M5: "TIMEFRAME_M5",
        Timeframe.M15: "TIMEFRAME_M15",
        Timeframe.M30: "TIMEFRAME_M30",
        Timeframe.H1: "TIMEFRAME_H1",
        Timeframe.H4: "TIMEFRAME_H4",
        Timeframe.D1: "TIMEFRAME_D1",
        Timeframe.W1: "TIMEFRAME_W1",
        Timeframe.MN1: "TIMEFRAME_MN1",
    }
    attr = names.get(timeframe)
    return None if attr is None else int(getattr(mt5, attr))


class MT5MarketProvider(MarketDataProvider):
    """Feed de datos MT5 por polling, compartiendo el terminal con el broker.

    Args:
        connection: Conexión gestionada al terminal MT5 (compartida con el broker).
        poll_seconds: Cadencia del bucle de polling de ticks.
    """

    def __init__(self, connection: MT5Connection, *, poll_seconds: float = 0.5) -> None:
        super().__init__("mt5")
        self._conn = connection
        self._poll_seconds = max(0.05, poll_seconds)
        self._symbols: set[str] = set()
        self._state = ConnectionState.DISCONNECTED
        self._task: asyncio.Task[None] | None = None
        self._ticks_emitted = 0

    @property
    def capabilities(self) -> frozenset[ChannelType]:
        """Channels served by the MT5 feed."""
        return _CAPABILITIES

    @property
    def connection_state(self) -> ConnectionState:
        """Current streaming-connection state."""
        return self._state

    async def start(self) -> None:
        """Connect the terminal and begin the polling loop.

        Raises:
            ProviderError: Si la conexión con el terminal MT5 falla.
        """
        self._state = ConnectionState.CONNECTING
        try:
            await asyncio.to_thread(self._conn.connect)
        except Exception as exc:  # BrokerConnectionError u otros
            self._state = ConnectionState.DISCONNECTED
            raise ProviderError(
                f"No se pudo conectar al terminal MT5: {exc}",
                context={"provider": "mt5"},
                recoverable=True,
            ) from exc
        self._state = ConnectionState.CONNECTED
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop(), name="mt5-poll")
        _log.info("MT5 feed iniciado (poll=%.2fs)", self._poll_seconds)

    async def stop(self) -> None:
        """Stop the polling loop (never raises)."""
        self._state = ConnectionState.STOPPED
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(self._conn.disconnect)

    async def subscribe(self, symbol: str, channels: Sequence[ChannelType]) -> None:
        """Subscribe a symbol to tick polling (channels sólo informan capacidad)."""
        if not any(ch in _CAPABILITIES for ch in channels):
            return
        # OJO: NO normalizar a mayúsculas. Los símbolos MT5 de Exness llevan
        # sufijos en minúscula (p. ej. ``XAUUSDm``); ``.upper()`` los rompería y
        # ``symbol_info_tick`` devolvería None → sin ticks.
        self._symbols.add(symbol)
        # Asegura que el símbolo esté visible en Market Watch antes de leerlo.
        await asyncio.to_thread(self._select_symbol, symbol)

    async def unsubscribe(self, symbol: str) -> None:
        """Drop tick polling for a symbol."""
        self._symbols.discard(symbol)

    def status(self) -> dict[str, object]:
        """Diagnostic snapshot."""
        return {
            "provider": "mt5",
            "state": self._state.value,
            "symbols": sorted(self._symbols),
            "ticks_emitted": self._ticks_emitted,
            "connected": self._conn.connected,
        }

    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """MT5 no expone profundidad fiable por esta vía: no soportado."""
        raise ProviderError(
            "El proveedor MT5 no sirve snapshots de order book",
            context={"provider": "mt5", "symbol": symbol},
            recoverable=False,
        )

    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Fetch recent closed candles via ``copy_rates_from_pos``."""
        return await asyncio.to_thread(self._copy_rates, symbol, timeframe, limit)

    async def fetch_server_time(self) -> float:
        """Return the local Unix timestamp (MT5 ticks carry their own time)."""
        return time.time()

    # ------------------------------------------------------------------
    # Internos (todos corren en hilos vía to_thread; usan el lock del terminal)
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Poll every subscribed symbol and emit a Ticker each cycle."""
        while True:
            try:
                symbols = tuple(self._symbols)
                if symbols:
                    tickers = await asyncio.to_thread(self._poll_once, symbols)
                    for ticker in tickers:
                        self._emit(ticker)
                        self._ticks_emitted += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.warning("Fallo en el ciclo de polling MT5", exc_info=True)
            await asyncio.sleep(self._poll_seconds)

    def _poll_once(self, symbols: Sequence[str]) -> list[Ticker]:
        """Read a tick for each symbol (single locked terminal access)."""
        out: list[Ticker] = []
        mt5 = self._conn.mt5
        with self._conn.lock:
            for symbol in symbols:
                # El sistema usa el símbolo en mayúsculas; el terminal puede
                # exponerlo con otra caja (XAUUSDm). Se lee con el nombre real
                # pero se emite el Ticker con el símbolo del sistema.
                real = self._conn.resolve_symbol(symbol)
                tick = mt5.symbol_info_tick(real)
                if tick is None:
                    continue
                bid = float(getattr(tick, "bid", 0.0) or 0.0)
                ask = float(getattr(tick, "ask", 0.0) or 0.0)
                if bid <= 0 or ask <= 0:
                    continue
                exchange_ts = self._tick_time(tick)
                last = getattr(tick, "last", 0.0) or None
                out.append(
                    Ticker(
                        symbol=symbol,
                        provider="mt5",
                        bid=bid,
                        ask=ask,
                        last=float(last) if last else None,
                        exchange_ts=exchange_ts,
                        local_ts=utc_now(),
                    )
                )
        return out

    def _copy_rates(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        """Fetch closed candles from MT5 and normalize them."""
        mt5 = self._conn.mt5
        tf = _mt5_timeframe(mt5, timeframe)
        if tf is None:
            raise ProviderError(
                f"Timeframe {timeframe.value} no soportado por MT5",
                context={"provider": "mt5"},
                recoverable=False,
            )
        with self._conn.lock:
            real = self._conn.resolve_symbol(symbol)
            rates = mt5.copy_rates_from_pos(real, tf, 0, max(1, limit))
        if rates is None:
            return []
        seconds = timeframe.seconds or 60
        candles: list[Candle] = []
        for row in rates:
            opened = int(row["time"])
            volume = float(row["tick_volume"])
            candles.append(
                Candle(
                    symbol=symbol,
                    provider="mt5",
                    timeframe=timeframe,
                    start=datetime.fromtimestamp(opened, tz=UTC),
                    end=datetime.fromtimestamp(opened + seconds, tz=UTC),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=volume,
                    trades=int(volume),
                    closed=True,
                    source="provider",
                )
            )
        return candles

    def _select_symbol(self, symbol: str) -> None:
        """Ensure the symbol is selected in Market Watch (best effort)."""
        mt5 = self._conn.mt5
        real = self._conn.resolve_symbol(symbol)
        with self._conn.lock:
            try:
                mt5.symbol_select(real, True)
            except Exception:
                _log.warning("MT5 symbol_select falló para %s", real, exc_info=True)

    @staticmethod
    def _tick_time(tick: Any) -> datetime:
        """Best-effort UTC timestamp from an MT5 tick (msc has millis)."""
        msc = getattr(tick, "time_msc", 0) or 0
        if msc:
            return datetime.fromtimestamp(msc / 1000.0, tz=UTC)
        secs = getattr(tick, "time", 0) or 0
        return datetime.fromtimestamp(secs, tz=UTC) if secs else utc_now()

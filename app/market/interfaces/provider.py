"""Contrato que todo proveedor de datos de mercado debe cumplir.

El proveedor encapsula transporte (WS/REST/polling) **y** normalización: hacia
afuera solo salen objetos internos (:data:`~app.market.models.MarketObject`).
El resto del sistema jamás ve el formato original del exchange.
"""

import abc
from collections.abc import Callable, Sequence

from app.market.models import (
    Candle,
    ChannelType,
    ConnectionState,
    MarketObject,
    OrderBookDelta,
    Timeframe,
)

Sink = Callable[[MarketObject], None]
"""Callback no bloqueante que recibe cada objeto normalizado."""

StateCallback = Callable[[str, ConnectionState], None]
"""Callback síncrono ``(provider, estado)`` en cada transición de conexión."""


class MarketDataProvider(abc.ABC):
    """Base for every market-data provider (exchange/broker adapter).

    Args:
        name: Identificador único del proveedor (``binance``, ``mt5``...).
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._sink: Sink | None = None
        self._state_callback: StateCallback | None = None

    @property
    def name(self) -> str:
        """Unique provider identifier."""
        return self._name

    @property
    @abc.abstractmethod
    def capabilities(self) -> frozenset[ChannelType]:
        """Channels this provider can serve."""

    def set_sink(self, sink: Sink) -> None:
        """Register the non-blocking callback receiving normalized objects."""
        self._sink = sink

    def set_state_callback(self, callback: StateCallback) -> None:
        """Register the callback notified on connection-state transitions."""
        self._state_callback = callback

    def _emit(self, obj: MarketObject) -> None:
        """Push a normalized object downstream (no-op without sink)."""
        if self._sink is not None:
            self._sink(obj)

    def _notify_state(self, state: ConnectionState) -> None:
        """Notify a connection-state transition (no-op without callback)."""
        if self._state_callback is not None:
            self._state_callback(self._name, state)

    # ------------------------------------------------------------------
    # Ciclo de vida y suscripciones
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Open connections and begin streaming."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Close connections and release resources (never raises)."""

    @abc.abstractmethod
    async def subscribe(self, symbol: str, channels: Sequence[ChannelType]) -> None:
        """Subscribe a symbol to the given channels.

        Args:
            symbol: Símbolo interno (el proveedor lo traduce al suyo).
            channels: Canales solicitados (se ignoran los no soportados).
        """

    @abc.abstractmethod
    async def unsubscribe(self, symbol: str) -> None:
        """Drop every subscription for a symbol."""

    @property
    @abc.abstractmethod
    def connection_state(self) -> ConnectionState:
        """Current streaming-connection state."""

    @abc.abstractmethod
    def status(self) -> dict[str, object]:
        """Diagnostic snapshot (estado, métricas, suscripciones)."""

    # ------------------------------------------------------------------
    # REST — reconstrucción de estado cuando el streaming falla
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """Fetch a full order-book snapshot via REST.

        Args:
            symbol: Símbolo interno.
            depth: Niveles por lado.

        Returns:
            Delta con ``is_snapshot=True`` listo para el reconstructor.

        Raises:
            ProviderError: Si el proveedor no puede servir el snapshot.
        """

    @abc.abstractmethod
    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Fetch recent closed candles via REST (backfill / arranque).

        Args:
            symbol: Símbolo interno.
            timeframe: Timeframe solicitado.
            limit: Máximo de velas.

        Returns:
            Velas cerradas, ascendentes por tiempo.

        Raises:
            ProviderError: Si el proveedor no soporta velas históricas.
        """

    @abc.abstractmethod
    async def fetch_server_time(self) -> float:
        """Fetch the exchange server time as a Unix timestamp (seconds).

        Returns:
            Server time; usado para medir drift de reloj.

        Raises:
            ProviderError: Si el proveedor no expone la hora del servidor.
        """

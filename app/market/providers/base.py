"""Proveedor base sobre WebSocket + REST.

Implementa el ciclo común (conexión supervisada, resuscripción tras
reconectar, parseo → normalizador → sink) y deja a cada exchange solo su
protocolo: URLs, payloads de suscripción y traducción de símbolos.
"""

import abc
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from app.config.settings import MarketProviderSettings, MarketWSSettings
from app.core.exceptions import NormalizationError, ProviderError
from app.market.interfaces.normalizer import Normalizer
from app.market.interfaces.provider import MarketDataProvider
from app.market.models import (
    Candle,
    ChannelType,
    ConnectionState,
    OrderBookDelta,
    Timeframe,
)
from app.market.stream.connection import WSConnection, WSConnectionConfig
from app.market.stream.transport import WebsocketsTransport, WSTransport


class BaseWSProvider(MarketDataProvider):
    """Common machinery for WebSocket-streaming providers.

    Args:
        name: Identificador del proveedor.
        provider_settings: Credenciales/urls del proveedor.
        ws_settings: Parámetros de reconexión/heartbeat.
        normalizer: Traductor de mensajes crudos al modelo interno.
        transport_factory: Fábrica del transporte (inyectable en tests).
    """

    def __init__(
        self,
        name: str,
        provider_settings: MarketProviderSettings,
        ws_settings: MarketWSSettings,
        normalizer: Normalizer,
        *,
        transport_factory: Callable[[], WSTransport] | None = None,
    ) -> None:
        super().__init__(name)
        self._provider_settings = provider_settings
        self._ws_settings = ws_settings
        self._normalizer = normalizer
        self._transport_factory = transport_factory or (
            lambda: WebsocketsTransport(compression=ws_settings.compression)
        )
        self._connection: WSConnection | None = None
        self._subscriptions: dict[str, tuple[ChannelType, ...]] = {}
        self._rest: httpx.AsyncClient | None = None
        self._parse_errors = 0
        self._log = logging.getLogger(f"app.market.provider.{name}")

    # ------------------------------------------------------------------
    # Ganchos por exchange
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def _stream_url(self) -> str:
        """Public-stream WebSocket URL."""

    @abc.abstractmethod
    def _rest_url(self) -> str:
        """REST base URL (reconstrucción de estado)."""

    @abc.abstractmethod
    def _subscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        """Frames to send for subscribing a symbol to channels."""

    def _unsubscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        """Frames to send for unsubscribing (default: none)."""
        return []

    def _app_ping_payload(self) -> str:
        """Application-level ping frame ("" = solo ping de protocolo)."""
        return ""

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the supervised streaming connection."""
        if self._connection is not None:
            return
        config = WSConnectionConfig(
            name=self._name,
            url=self._provider_settings.ws_url or self._stream_url(),
            ping_interval_seconds=self._ws_settings.ping_interval_seconds,
            stale_after_seconds=self._ws_settings.stale_after_seconds,
            backoff_base_seconds=self._ws_settings.backoff_base_seconds,
            backoff_factor=self._ws_settings.backoff_factor,
            backoff_cap_seconds=self._ws_settings.backoff_cap_seconds,
            max_retries=self._ws_settings.max_retries,
            app_ping_payload=self._app_ping_payload(),
        )
        self._connection = WSConnection(
            config,
            transport_factory=self._transport_factory,
            on_message=self._on_raw_message,
            on_connected=self._on_connected,
            on_state_change=lambda state: self._notify_state(state),
        )
        await self._connection.start()

    async def stop(self) -> None:
        """Stop streaming and close the REST client."""
        if self._connection is not None:
            await self._connection.stop()
            self._connection = None
        if self._rest is not None:
            await self._rest.aclose()
            self._rest = None

    @property
    def connection(self) -> WSConnection | None:
        """The underlying supervised connection (para el WS manager)."""
        return self._connection

    @property
    def connection_state(self) -> ConnectionState:
        """Current streaming-connection state."""
        if self._connection is None:
            return ConnectionState.DISCONNECTED
        return self._connection.state

    @property
    def subscriptions(self) -> dict[str, tuple[ChannelType, ...]]:
        """Active subscriptions (symbol → channels)."""
        return dict(self._subscriptions)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot."""
        return {
            "provider": self._name,
            "state": self.connection_state.value,
            "subscriptions": {
                symbol: [channel.value for channel in channels]
                for symbol, channels in self._subscriptions.items()
            },
            "parse_errors": self._parse_errors,
            "connection": self._connection.status() if self._connection else None,
        }

    # ------------------------------------------------------------------
    # Suscripciones
    # ------------------------------------------------------------------

    async def subscribe(self, symbol: str, channels: Sequence[ChannelType]) -> None:
        """Subscribe a symbol; unsupported channels are dropped with a log."""
        supported = tuple(ch for ch in channels if ch in self.capabilities)
        skipped = set(channels) - set(supported)
        if skipped:
            self._log.info(
                "Provider '%s' does not support %s for %s — skipped",
                self._name,
                sorted(ch.value for ch in skipped),
                symbol,
            )
        if not supported:
            return
        self._subscriptions[symbol.upper()] = supported
        if self._connection is not None and self._connection.is_connected:
            for payload in self._subscribe_payloads(symbol.upper(), supported):
                await self._connection.send(payload)

    async def unsubscribe(self, symbol: str) -> None:
        """Drop the subscription for a symbol."""
        channels = self._subscriptions.pop(symbol.upper(), None)
        if channels is None:
            return
        if self._connection is not None and self._connection.is_connected:
            for payload in self._unsubscribe_payloads(symbol.upper(), channels):
                await self._connection.send(payload)

    async def _on_connected(self, was_reconnection: bool) -> None:
        """(Re)send every active subscription after connecting."""
        if self._connection is None:
            return
        for symbol, channels in self._subscriptions.items():
            for payload in self._subscribe_payloads(symbol, channels):
                await self._connection.send(payload)
        if was_reconnection:
            self._log.info("Resubscribed %d symbols after reconnection", len(self._subscriptions))

    # ------------------------------------------------------------------
    # Pipeline de mensajes
    # ------------------------------------------------------------------

    async def _on_raw_message(self, message: str) -> None:
        """Parse JSON → normalize → emit downstream."""
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._parse_errors += 1
            self._log.warning("Provider '%s' sent non-JSON frame", self._name)
            return
        if not isinstance(payload, dict):
            return
        try:
            for obj in self._normalizer.normalize(payload):
                self._emit(obj)
        except NormalizationError as exc:
            self._parse_errors += 1
            self._log.warning("Normalization failed for '%s': %s", self._name, exc)

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------

    def _rest_client(self) -> httpx.AsyncClient:
        """Lazily build the REST client."""
        if self._rest is None:
            self._rest = httpx.AsyncClient(
                base_url=self._provider_settings.rest_url or self._rest_url(),
                timeout=10.0,
            )
        return self._rest

    async def _rest_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET helper with uniform error handling.

        Raises:
            ProviderError: Ante fallo de red o HTTP != 2xx.
        """
        try:
            response = await self._rest_client().get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "REST request failed",
                context={"provider": self._name, "path": path, "error": repr(exc)},
            ) from exc

    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """Default: not supported (cada exchange lo implementa)."""
        raise ProviderError(
            "Order-book snapshot not implemented",
            context={"provider": self._name, "symbol": symbol},
        )

    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Default: not supported (cada exchange lo implementa)."""
        raise ProviderError(
            "Historical candles not implemented",
            context={"provider": self._name, "symbol": symbol},
        )

    async def fetch_server_time(self) -> float:
        """Default: not supported (cada exchange lo implementa)."""
        raise ProviderError("Server time not implemented", context={"provider": self._name})

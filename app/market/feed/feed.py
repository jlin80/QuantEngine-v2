"""MarketFeed: orquestador de proveedores y suscripciones.

La cara de control del Data Engine. Arranca los proveedores configurados,
rutea símbolos a proveedores, publica eventos de conexión y atiende las
reconstrucciones de order book vía REST cuando el streaming pierde secuencia.

Las estrategias futuras solo dirán ``subscribe("BTCUSDT")`` — el ruteo al
broker correcto vive en la configuración, no en el consumidor.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

from app.config.settings import MarketSettings
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.events.events import ConnectionLost, ConnectionRestored
from app.core.exceptions import EventBusError, ProviderError
from app.core.lifecycle import Service
from app.market.collector import TickCollector
from app.market.events import FeedSubscribed, FeedUnsubscribed, OrderBookResyncRequired
from app.market.interfaces.provider import MarketDataProvider
from app.market.models import ChannelType, ConnectionState, Timeframe
from app.market.providers.registry import ProviderRegistry
from app.market.stream.manager import WebSocketManager


class MarketFeed(Service):
    """Provider orchestration + symbol routing for the Data Engine.

    Args:
        settings: Sección ``market`` de la configuración.
        registry: Registro de proveedores.
        collector: Pipeline que recibe los objetos normalizados.
        bus: Event Bus del sistema.
        ws_manager: Registro central de conexiones WebSocket.
    """

    def __init__(
        self,
        settings: MarketSettings,
        registry: ProviderRegistry,
        collector: TickCollector,
        bus: EventBus,
        ws_manager: WebSocketManager,
    ) -> None:
        super().__init__("market_feed")
        self._settings = settings
        self._registry = registry
        self._collector = collector
        self._bus = bus
        self._ws_manager = ws_manager
        self._providers: dict[str, MarketDataProvider] = {}
        self._subscriptions: dict[str, str] = {}  # symbol -> provider
        self._pending_publishes: set[asyncio.Task[None]] = set()
        self._resync_subscription: Any = None
        self._log = logging.getLogger("app.market.feed")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _on_start(self) -> None:
        self._resync_subscription = self._bus.subscribe(
            self._on_resync_required, OrderBookResyncRequired
        )
        for symbol in self._settings.symbols:
            try:
                await self.subscribe(symbol)
            except ProviderError as exc:
                # Un proveedor no implementado no debe impedir el arranque.
                self._log.error("Cannot subscribe %s: %s", symbol, exc)

    async def _on_stop(self) -> None:
        if self._resync_subscription is not None:
            self._bus.unsubscribe(self._resync_subscription)
            self._resync_subscription = None
        for provider in self._providers.values():
            await provider.stop()
        self._providers.clear()

    async def healthcheck(self) -> bool:
        """Healthy si todo proveedor activo tiene su conexión establecida."""
        if not self.is_running:
            return False
        return all(
            provider.connection_state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING)
            for provider in self._providers.values()
        )

    # ------------------------------------------------------------------
    # Suscripciones (API pública del feed)
    # ------------------------------------------------------------------

    def _default_channels(self) -> list[ChannelType]:
        """Channels from configuration (invalid names ignored with a log)."""
        channels: list[ChannelType] = []
        for name in self._settings.channels:
            try:
                channels.append(ChannelType(name))
            except ValueError:
                self._log.warning("Unknown channel '%s' in configuration — ignored", name)
        return channels or [ChannelType.TICKER, ChannelType.TRADES]

    async def subscribe(
        self,
        symbol: str,
        channels: Sequence[ChannelType] | None = None,
        *,
        provider_name: str | None = None,
    ) -> None:
        """Subscribe a symbol without the caller knowing any broker.

        Args:
            symbol: Símbolo interno (``BTCUSDT``, ``XAUUSD``...).
            channels: Canales deseados (default: los de configuración).
            provider_name: Forzar proveedor (default: ruteo configurado).

        Raises:
            ProviderError: Si el proveedor ruteado no puede arrancar.
        """
        symbol = symbol.upper()
        name = provider_name or self._settings.provider_for(symbol)
        provider = await self._ensure_provider(name)
        selected = tuple(channels) if channels is not None else tuple(self._default_channels())
        await provider.subscribe(symbol, selected)
        self._subscriptions[symbol] = name
        await self._publish(
            FeedSubscribed(
                source="market_feed",
                symbol=symbol,
                provider=name,
                channels=tuple(channel.value for channel in selected),
            )
        )
        self._log.info(
            "Subscribed %s via %s (%s)", symbol, name, ",".join(c.value for c in selected)
        )
        if ChannelType.ORDERBOOK in selected:
            self._schedule_initial_snapshot(symbol)
        if self._settings.backfill_on_subscribe > 0:
            self._schedule_backfill(symbol, provider)

    def _schedule_backfill(self, symbol: str, provider: MarketDataProvider) -> None:
        """Seed candle history so strategies reach warmup without waiting.

        Trae velas cerradas del proveedor (``fetch_candles``) para cada
        timeframe configurado y las siembra en el store SIN emitir eventos: da
        la ventana mínima desde el arranque sin disparar señales sobre histórico.
        """
        limit = self._settings.backfill_on_subscribe
        timeframes: list[Timeframe] = []
        for name in self._settings.timeframes:
            try:
                timeframes.append(Timeframe(name))
            except ValueError:
                continue

        async def _backfill() -> None:
            for timeframe in timeframes:
                try:
                    candles = await provider.fetch_candles(symbol, timeframe, limit)
                except (ProviderError, NotImplementedError) as exc:
                    self._log.debug("Backfill %s %s skipped: %s", symbol, timeframe.value, exc)
                    continue
                except Exception:  # noqa: BLE001 — el backfill nunca tumba la suscripción
                    self._log.warning(
                        "Backfill %s %s failed", symbol, timeframe.value, exc_info=True
                    )
                    continue
                primed = await self._collector.prime_candles(candles)
                if primed:
                    self._log.info(
                        "Backfilled %d %s candles for %s", primed, timeframe.value, symbol
                    )

        task = asyncio.create_task(_backfill())
        self._pending_publishes.add(task)
        task.add_done_callback(self._pending_publishes.discard)

    def _schedule_initial_snapshot(self, symbol: str) -> None:
        """Bootstrap the incremental order book with an initial REST snapshot.

        El stream ``@depth`` es **diferencial**: sin un snapshot inicial el libro
        nunca se sincroniza (``resync_needed`` sólo se dispara al *perder* una
        sincronización que antes existía). Se lanza en segundo plano tras un breve
        margen para que ya haya deltas en buffer; un hueco de secuencia real se
        autorepara luego vía :class:`OrderBookResyncRequired`.
        """

        async def _bootstrap() -> None:
            await asyncio.sleep(2.0)
            if not await self.resync_orderbook(symbol):
                self._log.debug("Order book for %s not ready yet (no initial snapshot)", symbol)

        task = asyncio.create_task(_bootstrap())
        self._pending_publishes.add(task)
        task.add_done_callback(self._pending_publishes.discard)

    async def unsubscribe(self, symbol: str) -> None:
        """Drop the subscription for a symbol."""
        symbol = symbol.upper()
        name = self._subscriptions.pop(symbol, None)
        if name is None:
            return
        provider = self._providers.get(name)
        if provider is not None:
            await provider.unsubscribe(symbol)
        await self._publish(FeedUnsubscribed(source="market_feed", symbol=symbol, provider=name))

    @property
    def subscriptions(self) -> dict[str, str]:
        """Active subscriptions (symbol → provider name)."""
        return dict(self._subscriptions)

    def provider_of(self, symbol: str) -> str | None:
        """Provider serving a symbol (``None`` si no está suscrito)."""
        return self._subscriptions.get(symbol.upper())

    def is_symbol_connected(self, symbol: str) -> bool:
        """Whether the provider connection behind a symbol is live."""
        name = self._subscriptions.get(symbol.upper())
        if name is None:
            return False
        provider = self._providers.get(name)
        return provider is not None and provider.connection_state is ConnectionState.CONNECTED

    # ------------------------------------------------------------------
    # Proveedores
    # ------------------------------------------------------------------

    async def _ensure_provider(self, name: str) -> MarketDataProvider:
        """Build, wire and start a provider on first use."""
        provider = self._providers.get(name)
        if provider is not None:
            return provider
        provider = self._registry.create(name)
        provider.set_sink(self._collector.submit)
        provider.set_state_callback(self._on_provider_state)
        await provider.start()
        self._providers[name] = provider
        connection = getattr(provider, "connection", None)
        if connection is not None:
            self._ws_manager.register(f"{name}:public", connection)
        return provider

    def _on_provider_state(self, provider: str, state: ConnectionState) -> None:
        """Translate connection transitions into system events."""
        if state is ConnectionState.RECONNECTING:
            self._metrics_reconnect()
            self._publish_nowait(
                ConnectionLost(source="market_feed", target=provider, detail="stream down")
            )
        elif state is ConnectionState.CONNECTED:
            self._publish_nowait(ConnectionRestored(source="market_feed", target=provider))

    def _metrics_reconnect(self) -> None:
        """Count a reconnection in the shared metrics."""
        self._collector.metrics.reconnections += 1

    # ------------------------------------------------------------------
    # Reconstrucción de order book vía REST
    # ------------------------------------------------------------------

    async def _on_resync_required(self, event: Event) -> None:
        """Fetch a REST snapshot when the incremental book loses sequence."""
        if not isinstance(event, OrderBookResyncRequired):
            return
        provider = self._providers.get(event.provider)
        if provider is None:
            return
        try:
            snapshot = await provider.fetch_orderbook_snapshot(
                event.symbol, self._settings.orderbook_depth
            )
        except ProviderError as exc:
            self._log.warning("Order-book resync failed for %s: %s", event.symbol, exc)
            return
        self._collector.submit(snapshot)
        self._log.info("Order book for %s rebuilt via REST", event.symbol)

    async def resync_orderbook(self, symbol: str) -> bool:
        """Force a REST order-book rebuild (scheduler/manual).

        Returns:
            ``True`` si el snapshot se obtuvo y se encoló.
        """
        name = self._subscriptions.get(symbol.upper())
        provider = self._providers.get(name) if name else None
        if provider is None:
            return False
        try:
            snapshot = await provider.fetch_orderbook_snapshot(
                symbol.upper(), self._settings.orderbook_depth
            )
        except ProviderError as exc:
            self._log.warning("Manual resync failed for %s: %s", symbol, exc)
            return False
        self._collector.submit(snapshot)
        return True

    # ------------------------------------------------------------------
    # Diagnóstico / utilidades
    # ------------------------------------------------------------------

    async def measure_clock_drift(self) -> dict[str, float]:
        """Compare local clock against each provider's server time.

        Returns:
            Drift en segundos por proveedor (positivo = reloj local adelantado).
        """
        drifts: dict[str, float] = {}
        for name, provider in self._providers.items():
            try:
                server_time = await provider.fetch_server_time()
            except ProviderError:
                continue
            drifts[name] = time.time() - server_time
        return drifts

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "subscriptions": dict(self._subscriptions),
            "providers": {name: provider.status() for name, provider in self._providers.items()},
            "websockets": self._ws_manager.status(),
            "collector_queue_depth": self._collector.queue_depth,
        }

    async def _publish(self, event: Event) -> None:
        """Publish tolerating a saturated/stopped bus."""
        try:
            await self._bus.publish(event)
        except EventBusError as exc:
            self._log.warning("Event publish failed: %s", exc)

    def _publish_nowait(self, event: Event) -> None:
        """Sync-context publish (crea una tarea si el loop está vivo)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._publish(event))
        self._pending_publishes.add(task)
        task.add_done_callback(self._pending_publishes.discard)

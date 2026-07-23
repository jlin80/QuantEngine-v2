"""Conexión WebSocket supervisada: reconexión, heartbeat y métricas.

Máquina de estados de una conexión de streaming:

    CONNECTING → CONNECTED → (fallo) → RECONNECTING → CONNECTING → ...
                            ↘ STOPPED (stop() explícito)

Reintentos con backoff exponencial + jitter, heartbeat con ping de protocolo
(y payload de aplicación opcional), detección de conexión muda y
reconstrucción de estado vía callback ``on_connected`` (resuscripción).
"""

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import ProviderError
from app.market.models import ConnectionState
from app.market.stream.metrics import LatencyTracker
from app.market.stream.transport import WSTransport

MessageCallback = Callable[[str], Awaitable[None]]
"""Callback asíncrono invocado con cada frame de texto recibido."""

ConnectedCallback = Callable[[bool], Awaitable[None]]
"""Callback tras (re)conectar; el argumento indica si es una reconexión."""

StateChangeCallback = Callable[[ConnectionState], None]
"""Callback síncrono en cada transición de estado."""


@dataclass(kw_only=True, slots=True)
class WSConnectionConfig:
    """Parámetros de una conexión WebSocket supervisada.

    Attributes:
        name: Nombre para logs y métricas.
        url: URL del stream.
        ping_interval_seconds: Cada cuánto enviar ping (0 = sin heartbeat).
        stale_after_seconds: Silencio máximo antes de forzar reconexión.
        backoff_base_seconds: Espera inicial entre reintentos.
        backoff_factor: Multiplicador exponencial.
        backoff_cap_seconds: Espera máxima entre reintentos.
        max_retries: Reintentos consecutivos máximos (0 = infinitos).
        app_ping_payload: Payload de ping a nivel aplicación ("" = solo
            ping de protocolo). Algunos exchanges (Bybit) lo requieren.
    """

    name: str
    url: str
    ping_interval_seconds: float = 20.0
    stale_after_seconds: float = 30.0
    backoff_base_seconds: float = 1.0
    backoff_factor: float = 2.0
    backoff_cap_seconds: float = 60.0
    max_retries: int = 0
    app_ping_payload: str = ""


class WSConnection:
    """One supervised streaming connection.

    Args:
        config: Parámetros de conexión y reintentos.
        transport_factory: Fábrica del transporte (inyectable en tests).
        on_message: Corrutina invocada con cada frame recibido.
        on_connected: Corrutina tras cada (re)conexión — resuscripción y
            reconstrucción de estado viven aquí.
        on_state_change: Callback síncrono por transición de estado.
    """

    def __init__(
        self,
        config: WSConnectionConfig,
        *,
        transport_factory: Callable[[], WSTransport],
        on_message: MessageCallback,
        on_connected: ConnectedCallback | None = None,
        on_state_change: StateChangeCallback | None = None,
    ) -> None:
        self._config = config
        self._transport_factory = transport_factory
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_state_change = on_state_change
        self._transport: WSTransport | None = None
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._state = ConnectionState.DISCONNECTED
        self._stop_requested = False
        self._connected_at: float | None = None
        self._disconnected_at: float | None = None
        self._last_message_at: float | None = None
        self._messages_received = 0
        self._reconnections = 0
        self._consecutive_failures = 0
        self.ping_latency = LatencyTracker()
        self._log = logging.getLogger(f"app.market.ws.{config.name}")

    # ------------------------------------------------------------------
    # Estado y métricas
    # ------------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Whether the connection is currently established."""
        return self._state is ConnectionState.CONNECTED

    @property
    def reconnections(self) -> int:
        """Reconexiones exitosas desde el arranque."""
        return self._reconnections

    @property
    def messages_received(self) -> int:
        """Frames recibidos desde el arranque."""
        return self._messages_received

    @property
    def seconds_since_last_message(self) -> float | None:
        """Silencio actual en segundos (``None`` si nunca llegó nada)."""
        if self._last_message_at is None:
            return None
        return time.monotonic() - self._last_message_at

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot for dashboards/health."""
        uptime = time.monotonic() - self._connected_at if self._connected_at else None
        return {
            "name": self._config.name,
            "url": self._config.url,
            "state": self._state.value,
            "messages_received": self._messages_received,
            "reconnections": self._reconnections,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "seconds_since_last_message": self.seconds_since_last_message,
            "ping_latency": self.ping_latency.to_dict(),
        }

    def _set_state(self, state: ConnectionState) -> None:
        """Transition state and notify."""
        if state is self._state:
            return
        self._state = state
        self._log.info("Connection '%s' → %s", self._config.name, state.value)
        if self._on_state_change is not None:
            self._on_state_change(state)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the supervised connect/receive loop."""
        if self._task is not None and not self._task.done():
            return
        self._stop_requested = False
        self._task = asyncio.create_task(self._run(), name=f"ws-{self._config.name}")

    async def stop(self) -> None:
        """Stop the loop and close the transport (never raises)."""
        self._stop_requested = True
        for task in (self._heartbeat_task, self._task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._heartbeat_task = None
        self._task = None
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
        self._set_state(ConnectionState.STOPPED)

    async def send(self, data: str) -> None:
        """Send a text frame over the live connection.

        Raises:
            ProviderError: Si la conexión no está establecida.
        """
        if self._transport is None or not self.is_connected:
            raise ProviderError(
                "Cannot send: connection not established",
                context={"connection": self._config.name, "state": self._state.value},
            )
        await self._transport.send(data)

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect/receive forever with exponential-backoff reconnection."""
        while not self._stop_requested:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except ProviderError as exc:
                self._consecutive_failures += 1
                self._log.warning(
                    "Connection '%s' failed (attempt %d): %s",
                    self._config.name,
                    self._consecutive_failures,
                    exc,
                )
            if self._stop_requested:
                break
            limit = self._config.max_retries
            if limit and self._consecutive_failures >= limit:
                self._log.error(
                    "Connection '%s' exhausted %d retries — giving up",
                    self._config.name,
                    limit,
                )
                self._set_state(ConnectionState.DISCONNECTED)
                return
            self._set_state(ConnectionState.RECONNECTING)
            await asyncio.sleep(self._backoff_delay())

    def _backoff_delay(self) -> float:
        """Exponential backoff with full jitter, capped."""
        exponent = max(0, self._consecutive_failures - 1)
        base = self._config.backoff_base_seconds * (self._config.backoff_factor**exponent)
        capped = min(base, self._config.backoff_cap_seconds)
        return capped * (0.5 + random.random() / 2.0)  # jitter, uso no criptográfico

    async def _connect_once(self) -> None:
        """One full connect → notify → receive-until-failure cycle."""
        self._set_state(ConnectionState.CONNECTING)
        transport = self._transport_factory()
        await transport.connect(self._config.url)
        self._transport = transport
        was_reconnection = self._reconnections > 0 or self._consecutive_failures > 0
        downtime = (
            time.monotonic() - self._disconnected_at
            if was_reconnection and self._disconnected_at is not None
            else 0.0
        )
        self._connected_at = time.monotonic()
        self._set_state(ConnectionState.CONNECTED)
        if was_reconnection:
            self._reconnections += 1
            self._log.info(
                "Connection '%s' recovered after %.1fs (attempt %d)",
                self._config.name,
                downtime,
                self._consecutive_failures,
            )
        if self._on_connected is not None:
            await self._on_connected(was_reconnection)
        self._consecutive_failures = 0
        self._start_heartbeat()
        try:
            await self._receive_loop(transport)
        finally:
            self._stop_heartbeat()
            self._disconnected_at = time.monotonic()
            await transport.close()
            self._transport = None

    async def _receive_loop(self, transport: WSTransport) -> None:
        """Receive frames until failure or staleness."""
        stale_after = self._config.stale_after_seconds
        while not self._stop_requested:
            try:
                if stale_after > 0:
                    message = await asyncio.wait_for(transport.receive(), timeout=stale_after)
                else:
                    message = await transport.receive()
            except TimeoutError:
                raise ProviderError(
                    "Connection went silent — forcing reconnect",
                    context={
                        "connection": self._config.name,
                        "stale_after_seconds": stale_after,
                    },
                ) from None
            self._messages_received += 1
            self._last_message_at = time.monotonic()
            try:
                await self._on_message(message)
            except Exception:
                # El fallo de un consumidor jamás tumba la conexión.
                self._log.exception("on_message handler failed for '%s'", self._config.name)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Launch the periodic ping task (protocol + optional app payload)."""
        if self._config.ping_interval_seconds <= 0:
            return
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"ws-heartbeat-{self._config.name}"
        )

    def _stop_heartbeat(self) -> None:
        """Cancel the heartbeat task if running."""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """Ping periodically; record RTT; app-level payload if configured."""
        while True:
            await asyncio.sleep(self._config.ping_interval_seconds)
            transport = self._transport
            if transport is None or not self.is_connected:
                continue
            try:
                rtt = await transport.ping()
                self.ping_latency.observe(rtt)
                if self._config.app_ping_payload:
                    await transport.send(self._config.app_ping_payload)
            except ProviderError as exc:
                self._log.warning(
                    "Heartbeat failed for '%s': %s — connection will recycle",
                    self._config.name,
                    exc,
                )
                return

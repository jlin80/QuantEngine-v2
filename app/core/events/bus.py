"""Event Bus asíncrono — columna vertebral de la comunicación entre módulos.

Diseño:
    * Publicación no bloqueante: los eventos entran a una cola interna y un
      worker los despacha; un publicador nunca espera a los suscriptores.
    * Aislamiento de errores: la excepción de un handler se registra y se
      contabiliza, pero jamás afecta a otros handlers ni al bus.
    * Suscripción por tipo (incluye subclases) o comodín (todos los eventos).
    * Métricas internas expuestas para el Health Monitor.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.events.base import Event
from app.core.exceptions import EventBusError
from app.core.lifecycle import Service

EventHandler = Callable[[Event], Awaitable[None]]
"""Firma de un suscriptor: corrutina que recibe el evento."""

ErrorCallback = Callable[[str, Exception], None]
"""Callback síncrono invocado cuando un handler falla (hook para el watchdog)."""


@dataclass(frozen=True, slots=True)
class Subscription:
    """Handle devuelto por ``subscribe`` para poder desuscribirse."""

    event_type: type[Event] | None
    handler: EventHandler


@dataclass(slots=True)
class BusStats:
    """Contadores internos del bus (para health/diagnóstico)."""

    published: int = 0
    dispatched: int = 0
    handler_errors: int = 0
    dropped: int = 0
    queue_size: int = 0
    subscribers: int = 0
    dead_letters: int = 0

    def to_dict(self) -> dict[str, int]:
        """Serialize counters to a plain dict."""
        return {
            "published": self.published,
            "dispatched": self.dispatched,
            "handler_errors": self.handler_errors,
            "dropped": self.dropped,
            "queue_size": self.queue_size,
            "subscribers": self.subscribers,
            "dead_letters": self.dead_letters,
        }


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """Evento cuyo despacho falló en al menos un handler."""

    event: Event
    handler_name: str
    error: str


class EventBus(Service):
    """Asynchronous publish/subscribe event bus.

    Args:
        max_queue_size: Backpressure limit; beyond it ``publish`` raises.
        dead_letter_limit: How many failed dispatches to retain for diagnosis.
    """

    def __init__(self, *, max_queue_size: int = 10_000, dead_letter_limit: int = 100) -> None:
        super().__init__("event_bus")
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers: dict[type[Event] | None, list[EventHandler]] = {}
        self._worker_task: asyncio.Task[None] | None = None
        self._stats = BusStats()
        self._dead_letters: deque[DeadLetter] = deque(maxlen=dead_letter_limit)
        self._error_callbacks: list[ErrorCallback] = []
        self._log = logging.getLogger("app.event_bus")

    # ------------------------------------------------------------------
    # Suscripción
    # ------------------------------------------------------------------

    def subscribe(
        self, handler: EventHandler, event_type: type[Event] | None = None
    ) -> Subscription:
        """Register a handler for an event type (or every event).

        Args:
            handler: Coroutine function invoked with each matching event.
            event_type: Event class to match (subclasses included). ``None``
                subscribes to every event (wildcard).

        Returns:
            A :class:`Subscription` handle usable with :meth:`unsubscribe`.
        """
        self._subscribers.setdefault(event_type, []).append(handler)
        self._stats.subscribers += 1
        return Subscription(event_type=event_type, handler=handler)

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a previously registered handler (no-op if absent)."""
        handlers = self._subscribers.get(subscription.event_type, [])
        if subscription.handler in handlers:
            handlers.remove(subscription.handler)
            self._stats.subscribers -= 1

    def add_error_callback(self, callback: ErrorCallback) -> None:
        """Register a sync callback fired when any handler raises."""
        self._error_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Enqueue an event for asynchronous dispatch.

        Args:
            event: The event instance to distribute.

        Raises:
            EventBusError: If the bus is not running or the queue is full.
        """
        if not self.is_running:
            raise EventBusError(
                "Cannot publish: event bus is not running",
                context={"event": event.name, "state": str(self.state)},
            )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            self._stats.dropped += 1
            raise EventBusError(
                "Event queue is full — event dropped",
                context={"event": event.name, "queue_size": self._queue.qsize()},
            ) from exc
        self._stats.published += 1

    # ------------------------------------------------------------------
    # Ciclo de vida / despacho
    # ------------------------------------------------------------------

    async def _on_start(self) -> None:
        self._worker_task = asyncio.create_task(self._dispatch_loop(), name="event-bus-worker")

    async def _on_stop(self) -> None:
        # Drenar lo pendiente con un límite de tiempo y cancelar el worker.
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except TimeoutError:
            self._log.warning("Event queue not fully drained on shutdown")
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

    async def _dispatch_loop(self) -> None:
        """Consume the queue forever, dispatching each event."""
        while True:
            event = await self._queue.get()
            try:
                await self._dispatch(event)
            finally:
                self._queue.task_done()

    async def _dispatch(self, event: Event) -> None:
        """Run every matching handler concurrently with error isolation."""
        handlers = self._handlers_for(event)
        if not handlers:
            return
        await asyncio.gather(*(self._run_handler(handler, event) for handler in handlers))
        self._stats.dispatched += 1

    def _handlers_for(self, event: Event) -> list[EventHandler]:
        """Collect handlers matching the event type, its bases and wildcard."""
        matched: list[EventHandler] = []
        for event_type, handlers in self._subscribers.items():
            if event_type is None or isinstance(event, event_type):
                matched.extend(handlers)
        return matched

    async def _run_handler(self, handler: EventHandler, event: Event) -> None:
        """Execute one handler, isolating and recording any failure."""
        try:
            await handler(event)
        except Exception as exc:
            handler_name = getattr(handler, "__qualname__", repr(handler))
            self._stats.handler_errors += 1
            self._dead_letters.append(
                DeadLetter(event=event, handler_name=handler_name, error=repr(exc))
            )
            self._stats.dead_letters = len(self._dead_letters)
            self._log.exception(
                "Handler '%s' failed for event '%s' (id=%s)",
                handler_name,
                event.name,
                event.event_id,
            )
            for callback in self._error_callbacks:
                try:
                    callback(handler_name, exc)
                except Exception:
                    self._log.exception("Error callback failed")

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------

    @property
    def stats(self) -> BusStats:
        """Live counters (queue size refreshed on access)."""
        self._stats.queue_size = self._queue.qsize()
        return self._stats

    @property
    def dead_letters(self) -> list[DeadLetter]:
        """Snapshot of the most recent failed dispatches."""
        return list(self._dead_letters)

    async def healthcheck(self) -> bool:
        """Healthy while running and the worker task is alive."""
        return self.is_running and self._worker_task is not None and not self._worker_task.done()

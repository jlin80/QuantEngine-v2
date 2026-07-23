"""Servicio de notificaciones de ejecución (desacoplado por eventos).

Se suscribe al Event Bus, traduce cada evento de ejecución a un embed de
Discord con las plantillas y lo entrega a través del ``NotificationService``
global. El Execution Engine no conoce a este servicio: sólo publica eventos.
"""

import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from app.core.events.base import Event
from app.core.events.bus import EventBus, Subscription
from app.core.lifecycle import Service
from app.execution import events as ev
from app.execution.events import DiscordNotificationFailed, DiscordNotificationSent
from app.execution.notifications import templates
from app.notifications.models import Notification
from app.notifications.service import NotificationService

_BUILDERS: dict[type[Event], Callable[[Any], Notification]] = {
    ev.PositionOpened: templates.position_opened,
    ev.PositionClosed: templates.position_closed,
    ev.StopMoved: templates.stop_moved,
    ev.BreakEvenActivated: templates.break_even,
    ev.TrailingUpdated: templates.trailing_updated,
    ev.OrderRejected: templates.order_rejected,
    ev.RiskTriggered: templates.risk_triggered,
    ev.KillSwitchTriggered: templates.kill_switch,
    ev.CircuitBreakerTriggered: templates.circuit_breaker,
}


class ExecutionNotifier(Service):
    """Fan execution events out to Discord as rich embeds.

    Args:
        notifications: Servicio de notificaciones global (canal Discord).
        bus: Event Bus del sistema.
        notify_stops: Si notifica cada movimiento de stop/trailing (puede ser
            ruidoso; los cierres y break-even siempre se notifican).
    """

    def __init__(
        self,
        notifications: NotificationService,
        bus: EventBus,
        *,
        notify_stops: bool = False,
    ) -> None:
        super().__init__("execution_notifier")
        self._notifications = notifications
        self._bus = bus
        self._notify_stops = notify_stops
        self._subscriptions: list[Subscription] = []
        self._sent = 0
        self._recent: deque[dict[str, str]] = deque(maxlen=50)
        self._log = logging.getLogger("app.execution.notifier")

    async def _on_start(self) -> None:
        """Subscribe to every execution event with a template."""
        for event_type in _BUILDERS:
            self._subscriptions.append(self._bus.subscribe(self._handle, event_type))

    async def _on_stop(self) -> None:
        """Unsubscribe from the bus."""
        for subscription in self._subscriptions:
            self._bus.unsubscribe(subscription)
        self._subscriptions.clear()

    async def _handle(self, event: Event) -> None:
        """Build and deliver the embed for a supported execution event."""
        if isinstance(event, ev.StopMoved | ev.TrailingUpdated) and not self._notify_stops:
            return
        builder = _BUILDERS.get(type(event))
        if builder is None:
            return
        await self._deliver(builder(event))

    async def send_report(
        self, title: str, snapshot: dict[str, Any], performance: dict[str, Any]
    ) -> None:
        """Deliver a periodic performance report embed."""
        await self._deliver(templates.report(title, snapshot, performance))

    async def _deliver(self, notification: Notification) -> None:
        """Send a notification and announce success/failure on the bus."""
        try:
            await self._notifications.notify(notification)
            self._sent += 1
            self._recent.append(
                {
                    "title": notification.title,
                    "level": notification.level.value,
                    "timestamp": notification.timestamp.isoformat(),
                }
            )
            await self._publish(
                DiscordNotificationSent(
                    source="execution_notifier",
                    title=notification.title,
                    level=notification.level.value,
                )
            )
        except Exception as exc:  # el canal es best-effort; no debe propagar
            self._log.error("Fallo entregando notificación '%s': %r", notification.title, exc)
            await self._publish(
                DiscordNotificationFailed(
                    source="execution_notifier",
                    title=notification.title,
                    error=repr(exc),
                )
            )

    async def _publish(self, event: Event) -> None:
        """Publish tolerating a stopped/saturated bus."""
        try:
            await self._bus.publish(event)
        except Exception:
            self._log.debug("No se pudo publicar %s", event.name)

    @property
    def stats(self) -> dict[str, int]:
        """Delivery counters."""
        return {"sent": self._sent}

    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        """Last delivered notifications (newest last)."""
        return list(self._recent)[-limit:]

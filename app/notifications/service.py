"""Servicio de notificaciones desacoplado de los canales concretos."""

import asyncio
import logging

from app.core.interfaces.notifications import NotificationChannel
from app.core.lifecycle import Service
from app.notifications.models import Notification, NotificationLevel


class NotificationService(Service):
    """Fan-out of operational notifications to registered channels.

    La entrega es *best effort*: el fallo de un canal se registra y se
    contabiliza pero nunca interrumpe al llamador ni a otros canales.

    Args:
        min_level: Minimum severity actually delivered.
        enabled: Global kill-switch (testing lo apaga por configuración).
    """

    def __init__(
        self,
        *,
        min_level: NotificationLevel = NotificationLevel.INFO,
        enabled: bool = True,
    ) -> None:
        super().__init__("notifications")
        self._channels: dict[str, NotificationChannel] = {}
        self._min_level = min_level
        self._enabled = enabled
        self._delivered = 0
        self._failed = 0
        self._log = logging.getLogger("app.notifications")

    def register_channel(self, channel: NotificationChannel) -> None:
        """Register a delivery channel by its name."""
        self._channels[channel.channel_name] = channel
        self._log.info("Notification channel registered: %s", channel.channel_name)

    @property
    def channel_names(self) -> list[str]:
        """Names of the registered channels."""
        return list(self._channels)

    @property
    def stats(self) -> dict[str, int]:
        """Delivery counters."""
        return {"delivered": self._delivered, "failed": self._failed}

    async def notify(self, notification: Notification) -> None:
        """Deliver a notification to every channel (best effort).

        Args:
            notification: The message to fan out.
        """
        if not self._enabled or not self._channels:
            return
        if notification.level.rank < self._min_level.rank:
            return
        results = await asyncio.gather(
            *(channel.send(notification) for channel in self._channels.values()),
            return_exceptions=True,
        )
        for channel_name, result in zip(self._channels, results, strict=True):
            if isinstance(result, BaseException):
                self._failed += 1
                self._log.error(
                    "Channel '%s' failed to deliver '%s': %r",
                    channel_name,
                    notification.title,
                    result,
                )
            else:
                self._delivered += 1

    async def send(
        self,
        title: str,
        message: str,
        *,
        level: NotificationLevel = NotificationLevel.INFO,
        fields: dict[str, str] | None = None,
        source: str = "system",
        channel: str = "",
    ) -> None:
        """Convenience builder + :meth:`notify`."""
        await self.notify(
            Notification(
                title=title,
                message=message,
                level=level,
                fields=fields or {},
                source=source,
                channel=channel,
            )
        )

    async def _on_start(self) -> None:
        """Nothing to acquire: channels are registered explicitly."""

    async def _on_stop(self) -> None:
        """Close every channel."""
        for channel in self._channels.values():
            await channel.close()

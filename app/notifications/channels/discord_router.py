"""Enrutador Discord multicanal (Fase 9).

La spec pide canales lógicos separados —Sistema, Trading, Errores, Backtesting,
Machine Learning, Producción, Reportes— cada uno con su propio webhook. Este
canal único (registrado como ``discord`` en el :class:`NotificationService`)
mantiene un webhook por canal lógico más uno por defecto, y decide a cuál va
cada notificación:

1. Si la notificación trae ``channel`` explícito y hay webhook para él, ahí va.
2. Si no, se mapea por ``source`` mediante una tabla de ruteo configurable.
3. Los errores/críticos van además al canal ``errores`` si existe.
4. Lo que no encaja cae al webhook por defecto.

Sigue siendo *best effort*: registrar la ausencia de un webhook nunca rompe la
entrega por los demás. Un solo webhook configurado equivale exactamente al
comportamiento de antes (todo al canal por defecto).
"""

import logging

import httpx

from app.notifications.channels.discord import DiscordWebhookChannel
from app.notifications.models import Notification, NotificationLevel

#: Ruteo por defecto fuente→canal lógico (configurable vía settings).
DEFAULT_ROUTING: dict[str, str] = {
    "engine": "sistema",
    "health_monitor": "sistema",
    "watchdog": "sistema",
    "scheduler": "sistema",
    "execution": "trading",
    "execution_engine": "trading",
    "risk": "trading",
    "ml": "ml",
    "ml_engine": "ml",
    "backtesting": "backtesting",
    "backtest": "backtesting",
    "production": "produccion",
    "kill_switch": "produccion",
    "safe_mode": "produccion",
    "recovery": "produccion",
    "reporting": "reportes",
    "improvement": "produccion",
}


class RoutedDiscordChannel:
    """Fan-in of a single notification to the right Discord webhook.

    Args:
        default_channel: Fallback webhook channel (the classic single webhook).
        logical: Mapping of logical channel name → webhook channel.
        routing: Mapping of notification ``source`` → logical channel name.
    """

    def __init__(
        self,
        default_channel: DiscordWebhookChannel,
        logical: dict[str, DiscordWebhookChannel] | None = None,
        routing: dict[str, str] | None = None,
    ) -> None:
        self._default = default_channel
        self._logical = logical or {}
        self._routing = {**DEFAULT_ROUTING, **(routing or {})}
        self._log = logging.getLogger("app.notifications")

    @property
    def channel_name(self) -> str:
        """Channel identifier (single registration in the service)."""
        return "discord"

    @property
    def logical_channels(self) -> list[str]:
        """Names of the configured logical channels (besides the default)."""
        return sorted(self._logical)

    def _resolve(self, notification: Notification) -> DiscordWebhookChannel:
        """Pick the webhook for a notification (see module docstring)."""
        # 1. Canal explícito.
        explicit = notification.channel.strip().lower()
        if explicit and explicit in self._logical:
            return self._logical[explicit]
        # 2. Errores/críticos → canal de errores si existe.
        if (
            notification.level in (NotificationLevel.ERROR, NotificationLevel.CRITICAL)
            and "errores" in self._logical
        ):
            return self._logical["errores"]
        # 3. Ruteo por fuente.
        logical = self._routing.get(notification.source.strip().lower())
        if logical and logical in self._logical:
            return self._logical[logical]
        # 4. Por defecto.
        return self._default

    async def send(self, notification: Notification) -> None:
        """Deliver the notification to its resolved webhook.

        Raises:
            NotificationError: If delivery to the resolved webhook fails.
        """
        await self._resolve(notification).send(notification)

    async def close(self) -> None:
        """Close every underlying webhook client."""
        await self._default.close()
        for channel in self._logical.values():
            await channel.close()


def build_routed_discord(
    default_webhook: str,
    logical_webhooks: dict[str, str],
    *,
    routing: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
    max_retries: int = 3,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RoutedDiscordChannel:
    """Build a :class:`RoutedDiscordChannel` from raw webhook URLs.

    Args:
        default_webhook: Fallback webhook URL (required).
        logical_webhooks: Mapping logical name → webhook URL (may be empty).
        routing: Extra source→logical overrides.
        timeout_seconds: Per-request timeout for every webhook.
        max_retries: Retry budget for every webhook.
        transport: Optional httpx transport override (tests).

    Returns:
        A configured routed channel.
    """
    default_channel = DiscordWebhookChannel(
        default_webhook,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        transport=transport,
    )
    logical: dict[str, DiscordWebhookChannel] = {}
    for name, url in logical_webhooks.items():
        if not url:
            continue
        logical[name.strip().lower()] = DiscordWebhookChannel(
            url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            transport=transport,
        )
    return RoutedDiscordChannel(default_channel, logical, routing)

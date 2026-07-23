"""Modelo de mensajes de notificación."""

import enum
from dataclasses import dataclass, field
from datetime import datetime

from app.utils.time import utc_now


class NotificationLevel(enum.StrEnum):
    """Severidad de la notificación (ordenable)."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Numeric rank used for min-level filtering."""
        order = {
            NotificationLevel.INFO: 0,
            NotificationLevel.SUCCESS: 1,
            NotificationLevel.WARNING: 2,
            NotificationLevel.ERROR: 3,
            NotificationLevel.CRITICAL: 4,
        }
        return order[self]


@dataclass(frozen=True, slots=True)
class Notification:
    """Operational notification delivered through channels.

    Attributes:
        title: Short headline.
        message: Body text (markdown supported by Discord).
        level: Severity level.
        fields: Optional key/value details rendered as embed fields.
        source: Module that emitted the notification.
        channel: Optional logical channel hint (``trading``/``errores``/
            ``ml``/``produccion``/``reportes``...). Empty lets the router pick
            by source; the delivery layer maps it to a concrete webhook.
        timestamp: UTC creation time.
    """

    title: str
    message: str
    level: NotificationLevel = NotificationLevel.INFO
    fields: dict[str, str] = field(default_factory=dict)
    source: str = "system"
    channel: str = ""
    timestamp: datetime = field(default_factory=utc_now)

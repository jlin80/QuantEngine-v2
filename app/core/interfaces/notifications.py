"""Contrato de canales de notificación.

En esta versión el único canal habilitado es Discord (Webhook), pero el
servicio de notificaciones opera exclusivamente contra esta interfaz para
permitir extensiones futuras sin tocar la lógica de negocio.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.notifications.models import Notification


@runtime_checkable
class NotificationChannel(Protocol):
    """Delivery channel for operational notifications."""

    @property
    def channel_name(self) -> str:
        """Short channel identifier (e.g. ``discord``)."""
        ...

    async def send(self, notification: "Notification") -> None:
        """Deliver a notification.

        Raises:
            NotificationError: If delivery definitively fails.
        """
        ...

    async def close(self) -> None:
        """Release channel resources (HTTP clients, sockets...)."""
        ...

"""Notificaciones de ejecución vía Discord (servicio desacoplado)."""

from app.execution.notifications.service import ExecutionNotifier

__all__ = ["ExecutionNotifier"]

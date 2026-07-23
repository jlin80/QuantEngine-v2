"""Servicio de notificaciones operativas.

Regla global del proyecto: TODO se notifica exclusivamente por Discord
(Webhook). El servicio es modular (interfaz ``NotificationChannel``) para
extensiones futuras, pero en esta versión solo se habilita Discord.
"""

from app.notifications.models import Notification, NotificationLevel
from app.notifications.service import NotificationService

__all__ = ["Notification", "NotificationLevel", "NotificationService"]

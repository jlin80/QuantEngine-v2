"""Contratos (Protocols) entre módulos.

Los módulos dependen de estas interfaces, nunca de implementaciones
concretas de otros módulos (Dependency Inversion).
"""

from app.core.interfaces.broker import ExecutionBroker
from app.core.interfaces.cache import CacheBackend
from app.core.interfaces.documentation import DocumentationBackend
from app.core.interfaces.notifications import NotificationChannel
from app.core.interfaces.repository import Repository

__all__ = [
    "CacheBackend",
    "DocumentationBackend",
    "ExecutionBroker",
    "NotificationChannel",
    "Repository",
]

"""Configuración centralizada del sistema.

Toda la configuración vive en variables de entorno (``.env`` + overlays por
ambiente en ``config/<env>.env``). Ningún valor se modifica desde el código.
"""

from app.config.environment import Environment
from app.config.settings import Settings, get_settings

__all__ = ["Environment", "Settings", "get_settings"]

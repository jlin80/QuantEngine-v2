"""Eventos de la vigilancia del pipeline (sólo primitivos, JSON-safe).

Se publican en el Event Bus para que las notificaciones y el dashboard
reaccionen sin acoplarse al vigilante.
"""

from dataclasses import dataclass

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class MarketDataBlind(Event):
    """El motor está descartando casi todos los datos de mercado.

    Sin datos válidos no hay velas, ni señales, ni operaciones — pero el
    proceso sigue vivo y respondiendo, así que ninguna comprobación de salud
    convencional lo detecta.
    """

    checked: int
    discarded: int
    discard_ratio: float
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class MarketDataRecovered(Event):
    """El descarte volvió a niveles normales tras una alarma de ceguera."""

    checked: int
    discard_ratio: float
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalDrought(Event):
    """Entran datos de mercado limpios y no sale ninguna señal."""

    minutes: float
    clean_samples: int
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class DataQualityDegraded(Event):
    """La calidad del dato cambió de estado (Bloque 11).

    Se publica en la **transición**, en las dos direcciones: ``degraded=True``
    al entrar y ``degraded=False`` al recuperarse. Repetirlo cada minuto
    convertiría la alarma en ruido; no anunciar la recuperación dejaría a quien
    la leyó creyendo que el problema sigue.
    """

    score: float
    risk_multiplier: float
    degraded: bool
    reasons: str = ""

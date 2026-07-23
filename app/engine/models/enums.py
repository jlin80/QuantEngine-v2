"""Enumeraciones del núcleo cuantitativo."""

import enum


class Direction(enum.StrEnum):
    """Dirección de una señal u oportunidad."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class DecisionAction(enum.StrEnum):
    """Acción final del Decision Engine (nunca ejecuta: solo decide)."""

    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    STAND_ASIDE = "stand_aside"


class SignalStatus(enum.StrEnum):
    """Ciclo de vida de una señal en el Signal Engine."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class Regime(enum.StrEnum):
    """Regímenes de mercado detectables."""

    TRENDING = "trending"
    RANGING = "ranging"
    EXPANSION = "expansion"
    COMPRESSION = "compression"
    BREAKOUT = "breakout"
    REVERSAL = "reversal"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


class Session(enum.StrEnum):
    """Sesiones de mercado (por hora UTC; pueden solaparse)."""

    ASIA = "asia"
    EUROPE = "europe"
    AMERICA = "america"


class VolatilityState(enum.StrEnum):
    """Clasificación gruesa de volatilidad para contexto/filtros."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class CadenceKind(enum.StrEnum):
    """Disparadores de ejecución de una estrategia."""

    EVERY_TICK = "every_tick"
    EVERY_SECOND = "every_second"
    EVERY_CANDLE = "every_candle"
    INTERVAL = "interval"

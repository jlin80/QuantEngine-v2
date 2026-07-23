"""Eventos de detección analítica (Fase 4).

Las estrategias los acumulan en ``AnalysisContext.detections``; el Strategy
Engine los publica tras cada evaluación — las estrategias siguen sin tocar
el bus directamente.
"""

from dataclasses import dataclass

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class LiquidityDetected(Event):
    """Liquidez relevante detectada (pool, sweep, stop hunt...)."""

    symbol: str
    kind: str  # pool | sweep | stop_hunt | grab
    level: float
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class FVGDetected(Event):
    """Fair Value Gap operable detectado."""

    symbol: str
    direction: str  # bullish | bearish
    top: float
    bottom: float


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBlockDetected(Event):
    """Order Block operable detectado."""

    symbol: str
    direction: str  # bullish | bearish
    top: float
    bottom: float
    mitigated: bool = False


@dataclass(frozen=True, kw_only=True, slots=True)
class VWAPCalculated(Event):
    """VWAP de referencia calculado por una estrategia."""

    symbol: str
    kind: str  # day | week | month | anchored
    value: float
    slope_pct_per_bar: float = 0.0


@dataclass(frozen=True, kw_only=True, slots=True)
class DeltaCalculated(Event):
    """Delta agresor relevante medido."""

    symbol: str
    delta: float
    aggression_ratio: float


@dataclass(frozen=True, kw_only=True, slots=True)
class CVDCalculated(Event):
    """CVD/pendiente del CVD medidos."""

    symbol: str
    cvd: float
    slope: float


@dataclass(frozen=True, kw_only=True, slots=True)
class VolumeProfileUpdated(Event):
    """Perfil de volumen recalculado."""

    symbol: str
    poc: float
    vah: float
    val: float


@dataclass(frozen=True, kw_only=True, slots=True)
class MomentumDetected(Event):
    """Impulso/momentum significativo detectado."""

    symbol: str
    direction: str  # up | down
    score: float


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyScoreUpdated(Event):
    """Una estrategia actualizó su score/confianza tras evaluar."""

    strategy: str
    symbol: str
    score: float
    confidence: float

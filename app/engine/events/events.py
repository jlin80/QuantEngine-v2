"""Eventos del núcleo cuantitativo (solo campos primitivos, JSON-safe).

``StrategyLoaded`` y ``SignalCreated`` viven en ``app.core.events.events``
desde la Fase 1 y se reutilizan tal cual.
"""

from dataclasses import dataclass

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalRejected(Event):
    """Una señal no superó validación, deduplicación o filtros."""

    strategy: str
    symbol: str
    reasons: str
    stage: str = "validation"  # validation | dedupe | filters


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalExpired(Event):
    """Una señal alcanzó su expiración sin decisión."""

    strategy: str
    symbol: str
    signal_id: str
    lifetime_seconds: float


@dataclass(frozen=True, kw_only=True, slots=True)
class ConsensusReached(Event):
    """El motor de consenso produjo un resultado para un símbolo."""

    symbol: str
    method: str
    direction: str
    score: float
    agreement: float
    participants: int


@dataclass(frozen=True, kw_only=True, slots=True)
class DecisionGenerated(Event):
    """El Decision Engine emitió una decisión (aceptada o no).

    ``strategy``/``strategy_category`` son la atribución de la decisión a la
    estrategia que más aportó al consenso. Viajan en el evento porque la
    ejecución no conoce ni el Decision Engine ni los plugins de estrategia
    —sólo el bus—, y necesita la atribución para aplicar el holding mínimo por
    estrategia y para segmentar el Trade Journal. Vacías si no se pudo atribuir.
    """

    decision_id: str
    symbol: str
    action: str
    accepted: bool
    score: float
    confidence: float
    summary: str
    strategy: str = ""
    strategy_category: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class FilterTriggered(Event):
    """Un filtro bloqueó una posible operación."""

    filter_name: str
    symbol: str
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class MarketRegimeChanged(Event):
    """El régimen detectado de un símbolo cambió."""

    symbol: str
    previous: str
    current: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ContextUpdated(Event):
    """El Market Context de un símbolo se recalculó."""

    symbol: str
    regime: str
    volatility: str
    sessions: tuple[str, ...] = ()
    spread_bps: float | None = None
    data_quality: float = 0.0


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyExecuted(Event):
    """Una estrategia terminó una evaluación (métrica de rendimiento)."""

    strategy: str
    symbol: str
    duration_ms: float
    produced_signal: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyFailed(Event):
    """Una estrategia lanzó una excepción (aislada por el engine)."""

    strategy: str
    symbol: str
    error: str


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyUnloaded(Event):
    """Una estrategia fue retirada del engine."""

    strategy: str

"""Modelos del núcleo cuantitativo: señales, contexto, consenso y decisión.

Una estrategia nunca devuelve "BUY": devuelve una :class:`StrategySignal`
con dirección, confianza, score, zona de entrada, razones y advertencias.
La decisión final es una :class:`Decision` del Decision Engine, siempre
explicable.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.engine.models.enums import (
    CadenceKind,
    DecisionAction,
    Direction,
    Regime,
    SignalStatus,
    VolatilityState,
)
from app.market.models import Timeframe


def _new_id() -> str:
    """Unique identifier for signals/decisions."""
    return uuid.uuid4().hex


def _iso(moment: datetime | None) -> str | None:
    """ISO-8601 or None passthrough."""
    return None if moment is None else moment.isoformat()


@dataclass(frozen=True, kw_only=True, slots=True)
class Cadence:
    """Con qué frecuencia se ejecuta una estrategia.

    Attributes:
        kind: Disparador (tick, segundo, vela cerrada, intervalo).
        timeframe: Timeframe de la vela (solo ``EVERY_CANDLE``).
        seconds: Periodo en segundos (``EVERY_SECOND``/``INTERVAL``).
    """

    kind: CadenceKind
    timeframe: Timeframe | None = None
    seconds: float = 1.0

    def describe(self) -> str:
        """Human-readable cadence."""
        if self.kind is CadenceKind.EVERY_CANDLE:
            return f"candle:{self.timeframe.value if self.timeframe else '?'}"
        if self.kind in (CadenceKind.EVERY_SECOND, CadenceKind.INTERVAL):
            return f"every:{self.seconds}s"
        return self.kind.value


@dataclass(frozen=True, kw_only=True, slots=True)
class EntryZone:
    """Banda de precios de entrada propuesta por una estrategia."""

    low: float
    high: float

    def to_dict(self) -> dict[str, float]:
        """JSON-safe dict."""
        return {"low": self.low, "high": self.high}


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategySignal:
    """Evaluación estructurada producida por una estrategia.

    Attributes:
        signal_id: Identificador único.
        strategy_name: Estrategia emisora.
        strategy_version: Versión de la estrategia.
        symbol: Símbolo evaluado.
        timestamp: Momento de la evaluación (UTC).
        direction: Dirección de la oportunidad.
        confidence: Confianza propia de la estrategia (0-1).
        score: Puntuación de la oportunidad (0-100).
        entry_zone: Banda de entrada propuesta.
        stop_loss: Stop propuesto.
        take_profit: Objetivo propuesto.
        risk_reward: Relación riesgo/beneficio estimada.
        market_context: Resumen del contexto usado (JSON-safe).
        reasons: Razones que sustentan la señal (nunca vacío).
        warnings: Advertencias/matices de la propia estrategia.
        required_confirmation: Si exige confirmación de otra estrategia.
        expiration: Cuándo deja de ser válida.
        metadata: Datos adicionales de la estrategia (JSON-safe).
    """

    signal_id: str = field(default_factory=_new_id)
    strategy_name: str
    strategy_version: str = "1.0"
    symbol: str
    timestamp: datetime
    direction: Direction
    confidence: float
    score: float
    entry_zone: EntryZone | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None
    market_context: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    required_confirmation: bool = False
    expiration: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def priority(self) -> float:
        """Prioridad de la señal (score ponderado por confianza)."""
        return self.score * self.confidence

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "signal_id": self.signal_id,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "timestamp": _iso(self.timestamp),
            "direction": self.direction.value,
            "confidence": self.confidence,
            "score": self.score,
            "entry_zone": self.entry_zone.to_dict() if self.entry_zone else None,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "market_context": self.market_context,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "required_confirmation": self.required_confirmation,
            "expiration": _iso(self.expiration),
            "priority": self.priority,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalRecord:
    """Una señal con su ciclo de vida (el historial nunca borra información)."""

    signal: StrategySignal
    status: SignalStatus
    status_reasons: tuple[str, ...] = ()
    recorded_at: datetime
    resolved_at: datetime | None = None

    @property
    def lifetime_seconds(self) -> float | None:
        """Vida de la señal hasta su resolución."""
        if self.resolved_at is None:
            return None
        return (self.resolved_at - self.recorded_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            **self.signal.to_dict(),
            "status": self.status.value,
            "status_reasons": list(self.status_reasons),
            "recorded_at": _iso(self.recorded_at),
            "resolved_at": _iso(self.resolved_at),
            "lifetime_seconds": self.lifetime_seconds,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class RegimeState:
    """Clasificación de régimen con sus métricas de soporte."""

    symbol: str
    primary: Regime
    tags: tuple[Regime, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)
    detected_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "primary": self.primary.value,
            "tags": [tag.value for tag in self.tags],
            "metrics": self.metrics,
            "detected_at": _iso(self.detected_at),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class MarketContext:
    """Fotografía del estado del mercado que consumen estrategias y filtros.

    Attributes:
        symbol: Símbolo evaluado.
        generated_at: Momento de generación (UTC).
        regime: Régimen detectado.
        sessions: Sesiones activas por hora UTC.
        volatility: Clasificación de volatilidad.
        atr: ATR absoluto del timeframe de contexto.
        atr_pct: ATR como % del precio.
        spread_bps: Spread actual en puntos básicos.
        spread_elevated: Si el spread supera el umbral configurado.
        volume_recent: Volumen agregado reciente.
        volume_sufficient: Si supera el mínimo configurado.
        news_blackout: Si hay ventana de noticias activa.
        data_quality: Calidad del dato 0-1 (conexión + frescura).
        last_price: Último precio conocido.
        extras: Métricas adicionales (JSON-safe).
    """

    symbol: str
    generated_at: datetime
    regime: RegimeState | None = None
    sessions: tuple[str, ...] = ()
    volatility: VolatilityState = VolatilityState.NORMAL
    atr: float | None = None
    atr_pct: float | None = None
    spread_bps: float | None = None
    spread_elevated: bool = False
    volume_recent: float | None = None
    volume_sufficient: bool = True
    news_blackout: bool = False
    data_quality: float = 0.0
    last_price: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """Compact JSON-safe summary (para adjuntar a señales/decisiones)."""
        return {
            "regime": self.regime.primary.value if self.regime else Regime.UNKNOWN.value,
            "sessions": list(self.sessions),
            "volatility": self.volatility.value,
            "atr_pct": self.atr_pct,
            "spread_bps": self.spread_bps,
            "data_quality": self.data_quality,
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "generated_at": _iso(self.generated_at),
            "regime": self.regime.to_dict() if self.regime else None,
            "sessions": list(self.sessions),
            "volatility": self.volatility.value,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "spread_bps": self.spread_bps,
            "spread_elevated": self.spread_elevated,
            "volume_recent": self.volume_recent,
            "volume_sufficient": self.volume_sufficient,
            "news_blackout": self.news_blackout,
            "data_quality": self.data_quality,
            "last_price": self.last_price,
            "extras": self.extras,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class FilterResult:
    """Resultado de un filtro sobre una posible operación."""

    name: str
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {"name": self.name, "passed": self.passed, "reason": self.reason}


@dataclass(frozen=True, kw_only=True, slots=True)
class ConsensusResult:
    """Salida del motor de consenso sobre un grupo de señales."""

    method: str
    symbol: str
    direction: Direction
    score: float
    agreement: float
    participants: tuple[str, ...] = ()
    contributions: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "method": self.method,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "score": self.score,
            "agreement": self.agreement,
            "participants": list(self.participants),
            "contributions": self.contributions,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Decision:
    """Decisión única y explicable del Decision Engine.

    Nunca es solo "no operar": ``explanation`` siempre detalla el porqué.
    """

    decision_id: str = field(default_factory=_new_id)
    symbol: str
    timestamp: datetime
    action: DecisionAction
    accepted: bool
    score: float
    confidence: float
    agreement: float
    consensus: ConsensusResult | None = None
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    signals_considered: tuple[str, ...] = ()
    filters_blocking: tuple[str, ...] = ()
    explanation: tuple[str, ...] = ()
    regime: str = Regime.UNKNOWN.value
    strategy_categories: dict[str, str] = field(default_factory=dict)
    context_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_strategy(self) -> str:
        """Estrategia que más aportó al consenso ("" si no hay consenso).

        La decisión es multi-estrategia por diseño, pero la ejecución necesita
        atribuir la posición a *una* para poder aplicarle su holding mínimo y
        segmentar métricas por estrategia. Se toma la de mayor contribución;
        a igualdad, la primera participante (orden estable del consenso) para
        que la atribución sea determinista y reproducible en tests.
        """
        if self.consensus is None:
            return ""
        contributions = self.consensus.contributions
        if not contributions:
            return self.consensus.participants[0] if self.consensus.participants else ""
        # `max` sobre el orden de inserción del dict conserva el primero en
        # caso de empate: determinista, sin depender del orden alfabético.
        best = ""
        best_value = float("-inf")
        for name, value in contributions.items():
            if value > best_value:
                best, best_value = name, value
        return best

    @property
    def primary_category(self) -> str:
        """Categoría de :attr:`primary_strategy` ("" si se desconoce)."""
        return self.strategy_categories.get(self.primary_strategy, "")

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "timestamp": _iso(self.timestamp),
            "action": self.action.value,
            "accepted": self.accepted,
            "score": self.score,
            "confidence": self.confidence,
            "agreement": self.agreement,
            "consensus": self.consensus.to_dict() if self.consensus else None,
            "confidence_breakdown": self.confidence_breakdown,
            "signals_considered": list(self.signals_considered),
            "filters_blocking": list(self.filters_blocking),
            "explanation": list(self.explanation),
            "regime": self.regime,
            "strategy_categories": self.strategy_categories,
            "primary_strategy": self.primary_strategy,
            "primary_category": self.primary_category,
            "context_summary": self.context_summary,
        }


@dataclass(kw_only=True, slots=True)
class StrategyStats:
    """Métricas de ejecución de una estrategia (para el dashboard)."""

    name: str
    version: str = "1.0"
    enabled: bool = True
    symbols: tuple[str, ...] = ()
    cadence: str = ""
    weight: float = 1.0
    runs: int = 0
    errors: int = 0
    skipped: int = 0
    signals_produced: int = 0
    last_run_at: datetime | None = None
    last_duration_ms: float | None = None
    avg_duration_ms: float | None = None
    last_signal_at: datetime | None = None
    last_score: float | None = None
    last_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "version": self.version,
            "enabled": self.enabled,
            "symbols": list(self.symbols),
            "cadence": self.cadence,
            "weight": self.weight,
            "runs": self.runs,
            "errors": self.errors,
            "skipped": self.skipped,
            "signals_produced": self.signals_produced,
            "last_run_at": _iso(self.last_run_at),
            "last_duration_ms": self.last_duration_ms,
            "avg_duration_ms": self.avg_duration_ms,
            "last_signal_at": _iso(self.last_signal_at),
            "last_score": self.last_score,
            "last_confidence": self.last_confidence,
        }

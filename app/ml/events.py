"""Eventos de dominio de la capa de Machine Learning (Fase 7).

Cada módulo de dominio posee sus propios eventos (igual que ``app.market`` o
``app.execution``). El ML publica hitos del aprendizaje continuo en el Event Bus
y el ``MLNotifier`` los traduce a embeds de Discord. Ningún evento de ML abre o
cierra posiciones: el ML asesora, el Decision Engine decide.
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelTrainingStarted(Event):
    """Training of a model began."""

    model_type: str
    dataset_size: int
    label: str = "win"


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelTrainingFinished(Event):
    """Training of a model finished with its validation metrics."""

    model_id: str
    model_type: str
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelTrainingFailed(Event):
    """Training could not complete."""

    model_type: str
    error: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelApproved(Event):
    """A model passed the validation gate and was approved for use."""

    model_id: str
    model_type: str
    metrics: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRejected(Event):
    """A model failed the validation gate and was rejected."""

    model_id: str
    model_type: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelActivated(Event):
    """A model became the active model (advisory only)."""

    model_id: str
    model_type: str
    previous_id: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRolledBack(Event):
    """The active model was rolled back to a previous version."""

    model_id: str
    restored_id: str


@dataclass(frozen=True, kw_only=True, slots=True)
class DriftDetected(Event):
    """A drift condition was detected (feature/concept/performance/model)."""

    kind: str  # "feature" | "concept" | "performance" | "model"
    metric: str
    value: float
    threshold: float
    action: str = "alert"  # "alert" | "reduce_confidence" | "schedule_retrain"
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelDegraded(Event):
    """A live model's measured performance degraded below its baseline."""

    model_id: str
    metric: str
    baseline: float
    current: float


@dataclass(frozen=True, kw_only=True, slots=True)
class PerformanceRecordAchieved(Event):
    """A new best value was reached for a tracked performance metric."""

    metric: str
    value: float
    scope: str = "global"  # "global" | strategy name | symbol


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyWeightsUpdated(Event):
    """The Meta Strategy Manager adjusted dynamic strategy weights."""

    weights: dict[str, float] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class MetaStrategyDecision(Event):
    """The Meta Strategy Manager took a governance decision on a strategy."""

    strategy: str
    action: str  # "keep" | "boost" | "reduce" | "disable" | "enable" | "experiment"
    detail: str = ""
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, slots=True)
class MLRecommendation(Event):
    """The AI advisor produced an explainable recommendation (advisory only)."""

    topic: str
    recommendation: str
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class MLNotificationSent(Event):
    """An ML Discord notification was delivered."""

    title: str
    level: str


@dataclass(frozen=True, kw_only=True, slots=True)
class MLNotificationFailed(Event):
    """An ML Discord notification failed to deliver."""

    title: str
    error: str


def to_metric_dict(metrics: dict[str, Any]) -> dict[str, float]:
    """Coerce a metrics mapping to plain floats (JSON/event-safe).

    Args:
        metrics: Métricas heterogéneas (pueden traer ``None`` o strings).

    Returns:
        Sólo las entradas numéricas, como ``float``.
    """
    out: dict[str, float] = {}
    for key, value in metrics.items():
        if isinstance(value, (bool, int | float)):
            out[key] = float(value)
    return out

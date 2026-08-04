"""Eventos de dominio del Quant Research Lab (Fase 10).

El laboratorio publica sus hitos en el Event Bus y el ``ResearchNotifier`` los
traduce a embeds de Discord. Ningún evento abre operaciones ni habilita live
trading: el laboratorio investiga, el operador decide la promoción final.
"""

from dataclasses import dataclass, field

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class ExperimentCreated(Event):
    """A new research experiment was registered."""

    experiment_id: str
    label: str
    kind: str
    hypothesis: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class ExperimentArchived(Event):
    """An experiment was archived (knowledge retained, never deleted)."""

    experiment_id: str
    label: str
    conclusions: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyGenerated(Event):
    """One or more experimental strategies were generated."""

    count: int
    symbol: str
    sample_name: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class OptimizationCompleted(Event):
    """An optimization run (genetic/bayesian/multi-objective) finished."""

    method: str
    objective: str
    evaluations: int
    best_score: float | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class FeatureValidated(Event):
    """A candidate feature passed (or failed) the Feature Lab gate."""

    feature: str
    valid: bool
    ic: float


@dataclass(frozen=True, kw_only=True, slots=True)
class FactorResearchCompleted(Event):
    """A factor research sweep produced a fresh ranking."""

    tested: int
    retained: int
    top_factor: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidateQualified(Event):
    """A strategy passed the full candidate pipeline (new candidate)."""

    genome_id: str
    name: str
    symbol: str
    objective: str
    score: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidateFailed(Event):
    """A strategy was rejected by the candidate pipeline."""

    genome_id: str
    name: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ShadowReportReady(Event):
    """A Shadow Mode comparison reached its conclusion."""

    official: str
    challenger: str
    better: bool
    significant: bool
    p_value: float
    effect_r: float


@dataclass(frozen=True, kw_only=True, slots=True)
class RankingUpdated(Event):
    """The strategy ranking was recomputed."""

    segment: str
    size: int
    leader: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyPromoted(Event):
    """A candidate was promoted (operator-approved, evidence-backed)."""

    genome_id: str
    name: str
    operator: str
    improvement: float = 0.0


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyRejected(Event):
    """A candidate/promotion was rejected."""

    genome_id: str
    name: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ResearchFailed(Event):
    """A research operation failed (best-effort; never stops the engine)."""

    operation: str
    error: str


def metric_fields(metrics: dict[str, float], keys: tuple[str, ...]) -> dict[str, str]:
    """Pick and format headline metrics for a Discord embed."""
    return {key: f"{metrics[key]:.3f}" for key in keys if key in metrics}


@dataclass(frozen=True, kw_only=True, slots=True)
class ResearchCycleRolledBack(Event):
    """El ciclo autónomo se desactivó solo porque el motor operativo se degradó.

    No se rearma solo: volver a activarlo es una decisión humana. Un rollback
    reversible automáticamente convertiría un problema persistente en un ciclo
    de encendido/apagado, más difícil de diagnosticar que el fallo original.
    """

    triggers: tuple[str, ...]
    detail: str = ""


__all__ = [
    "CandidateFailed",
    "CandidateQualified",
    "ExperimentArchived",
    "ExperimentCreated",
    "FactorResearchCompleted",
    "FeatureValidated",
    "OptimizationCompleted",
    "RankingUpdated",
    "ResearchCycleRolledBack",
    "ResearchFailed",
    "ShadowReportReady",
    "StrategyGenerated",
    "StrategyPromoted",
    "StrategyRejected",
    "metric_fields",
]

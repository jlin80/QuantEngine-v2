"""Modelos de dominio del Quant Research Lab (Fase 10).

Vocabulario compartido por todo el laboratorio: genomas de estrategia (bloques
de señal + filtros de contexto), hipótesis, resultados por etapa del pipeline de
candidatas, ranking, features/factores validados, comparación de Shadow Mode,
validación en paper y decisiones de promoción. Todo es JSON-serializable y, salvo
los trackers que viven en sus módulos, inmutable.
"""

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import isoformat_utc, utc_now


def new_id(prefix: str) -> str:
    """Return a short unique identifier with a readable prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Combine(enum.StrEnum):
    """Cómo se combinan los bloques de señal de un genoma."""

    ALL = "all"  # todos los bloques deben coincidir (AND)
    ANY = "any"  # basta con uno (OR)
    MAJORITY = "majority"  # mayoría de bloques


class PipelineStage(enum.StrEnum):
    """Etapas del Candidate Pipeline (en orden)."""

    BACKTEST = "backtest"
    WALK_FORWARD = "walk_forward"
    MONTE_CARLO = "monte_carlo"
    ML_REVIEW = "ml_review"
    BENCHMARK = "benchmark"
    RISK_REVIEW = "risk_review"


class CandidateStatus(enum.StrEnum):
    """Ciclo de vida de una estrategia experimental."""

    GENERATED = "generated"
    EVALUATED = "evaluated"
    CANDIDATE = "candidate"  # superó el pipeline
    PAPER = "paper"  # en validación paper
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ExperimentStatus(enum.StrEnum):
    """Estado de un experimento en el Experiment Manager."""

    OPEN = "open"
    CLOSED = "closed"
    ARCHIVED = "archived"


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalBlock:
    """Un bloque de señal de un genoma (indicador o patrón de order flow).

    Attributes:
        kind: Tipo de bloque (``ema_cross``, ``vwap_reversion``, ``atr_breakout``,
            ``donchian_breakout``, ``momentum``, ``rsi_reversion``, ``macd``,
            ``delta_momentum``, ``cvd_trend``).
        params: Parámetros del bloque (todos numéricos, aptos para optimizar).
    """

    kind: str
    params: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {"kind": self.kind, "params": dict(self.params)}


@dataclass(frozen=True, kw_only=True, slots=True)
class ContextFilter:
    """Un filtro de contexto (horario, volatilidad, liquidez, régimen).

    Attributes:
        kind: Tipo de filtro (``session``, ``volatility``, ``liquidity``,
            ``trend_regime``).
        params: Parámetros del filtro.
    """

    kind: str
    params: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {"kind": self.kind, "params": dict(self.params)}


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyGenome:
    """Descripción declarativa de una estrategia experimental.

    Un genoma es *datos*, no código: el Strategy Generator lo produce siguiendo
    reglas cuantitativas y el compilador lo convierte en una ``DecisionSource``
    reproducible. El optimizador ajusta ``parameters`` sin tocar la estructura.

    Attributes:
        id: Identificador único.
        name: Nombre legible derivado de la estructura.
        symbol: Símbolo objetivo.
        timeframe: Timeframe objetivo.
        blocks: Bloques de señal combinados.
        filters: Filtros de contexto.
        combine: Cómo se combinan los bloques.
        allow_short: Si emite señales cortas.
        seed: Semilla de generación (reproducibilidad).
        metadata: Datos extra (hipótesis, familia, etc.).
    """

    id: str
    name: str
    symbol: str
    timeframe: str
    blocks: tuple[SignalBlock, ...]
    filters: tuple[ContextFilter, ...] = ()
    combine: Combine = Combine.ALL
    allow_short: bool = True
    seed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def parameters(self) -> dict[str, float]:
        """Flatten every block/filter parameter into a tunable vector.

        Las claves son estables: ``b{i}_{param}`` para bloques y ``f{i}_{param}``
        para filtros. El parameter lab reconstruye el genoma desde este vector.
        """
        flat: dict[str, float] = {}
        for i, block in enumerate(self.blocks):
            for key, value in block.params.items():
                flat[f"b{i}_{key}"] = value
        for i, filt in enumerate(self.filters):
            for key, value in filt.params.items():
                flat[f"f{i}_{key}"] = value
        return flat

    def with_parameters(self, values: dict[str, float]) -> "StrategyGenome":
        """Return a copy with block/filter parameters overridden by a flat vector.

        Args:
            values: Vector plano (mismas claves que :meth:`parameters`).

        Returns:
            Genoma clonado con los parámetros actualizados (misma estructura).
        """
        new_blocks: list[SignalBlock] = []
        for i, block in enumerate(self.blocks):
            merged = {
                key: float(values.get(f"b{i}_{key}", val)) for key, val in block.params.items()
            }
            new_blocks.append(SignalBlock(kind=block.kind, params=merged))
        new_filters: list[ContextFilter] = []
        for i, filt in enumerate(self.filters):
            merged_f = {
                key: float(values.get(f"f{i}_{key}", val)) for key, val in filt.params.items()
            }
            new_filters.append(ContextFilter(kind=filt.kind, params=merged_f))
        return StrategyGenome(
            id=self.id,
            name=self.name,
            symbol=self.symbol,
            timeframe=self.timeframe,
            blocks=tuple(new_blocks),
            filters=tuple(new_filters),
            combine=self.combine,
            allow_short=self.allow_short,
            seed=self.seed,
            metadata=dict(self.metadata),
        )

    def signature(self) -> str:
        """Stable structural hash (ignores id/name; sensible to params)."""
        payload = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "combine": self.combine.value,
            "allow_short": self.allow_short,
            "blocks": [b.to_dict() for b in self.blocks],
            "filters": [f.to_dict() for f in self.filters],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "combine": self.combine.value,
            "allow_short": self.allow_short,
            "seed": self.seed,
            "signature": self.signature(),
            "blocks": [b.to_dict() for b in self.blocks],
            "filters": [f.to_dict() for f in self.filters],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Hypothesis:
    """Una hipótesis de investigación registrada junto al experimento."""

    text: str
    rationale: str = ""
    expected_edge: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {"text": self.text, "rationale": self.rationale, "expected_edge": self.expected_edge}


@dataclass(frozen=True, kw_only=True, slots=True)
class StageResult:
    """Resultado de una etapa del Candidate Pipeline."""

    stage: PipelineStage
    passed: bool
    score: float | None = None
    detail: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "stage": self.stage.value,
            "passed": self.passed,
            "score": self.score,
            "detail": self.detail,
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidateReport:
    """Informe completo del paso de un genoma por el pipeline de validación."""

    genome_id: str
    name: str
    symbol: str
    status: CandidateStatus
    passed: bool
    objective: str
    score: float | None
    stages: tuple[StageResult, ...]
    statistics: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "genome_id": self.genome_id,
            "name": self.name,
            "symbol": self.symbol,
            "status": self.status.value,
            "passed": self.passed,
            "objective": self.objective,
            "score": self.score,
            "stages": [s.to_dict() for s in self.stages],
            "statistics": dict(self.statistics),
            "reasons": list(self.reasons),
            "created_at": isoformat_utc(self.created_at),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class RankingEntry:
    """Una fila del ranking de estrategias/candidatas."""

    rank: int
    genome_id: str
    name: str
    segment: str
    score: float
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "rank": self.rank,
            "genome_id": self.genome_id,
            "name": self.name,
            "segment": self.segment,
            "score": round(self.score, 6),
            "metrics": {k: round(v, 6) for k, v in self.metrics.items()},
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class FeatureReport:
    """Validación de una feature candidata en el Feature Lab."""

    name: str
    valid: bool
    coverage: float
    variance: float
    ic: float
    samples: int
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "name": self.name,
            "valid": self.valid,
            "coverage": round(self.coverage, 4),
            "variance": self.variance,
            "ic": round(self.ic, 4),
            "abs_ic": round(abs(self.ic), 4),
            "samples": self.samples,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class FactorReport:
    """Un factor investigado y puntuado por el Factor Lab."""

    name: str
    family: str
    ic: float
    hit_rate: float
    samples: int
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "name": self.name,
            "family": self.family,
            "ic": round(self.ic, 4),
            "abs_ic": round(abs(self.ic), 4),
            "hit_rate": round(self.hit_rate, 4),
            "samples": self.samples,
            "rank": self.rank,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ShadowComparison:
    """Comparación estadística de una estrategia challenger vs la oficial."""

    official: str
    challenger: str
    samples: int
    official_expectancy_r: float
    challenger_expectancy_r: float
    effect_r: float  # ventaja de la challenger en R por operación
    t_stat: float
    p_value: float
    significant: bool
    better: bool
    verdict: str
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "official": self.official,
            "challenger": self.challenger,
            "samples": self.samples,
            "official_expectancy_r": round(self.official_expectancy_r, 4),
            "challenger_expectancy_r": round(self.challenger_expectancy_r, 4),
            "effect_r": round(self.effect_r, 4),
            "t_stat": round(self.t_stat, 4),
            "p_value": round(self.p_value, 4),
            "significant": self.significant,
            "better": self.better,
            "verdict": self.verdict,
            "metrics": {k: round(v, 6) for k, v in self.metrics.items()},
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class PaperTrialStatus:
    """Estado de una validación en paper (¿ya maduró la candidata?)."""

    genome_id: str
    matured: bool
    days: float
    trades: int
    profit_factor: float
    drawdown_pct: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "genome_id": self.genome_id,
            "matured": self.matured,
            "days": round(self.days, 3),
            "trades": self.trades,
            "profit_factor": round(self.profit_factor, 4),
            "drawdown_pct": round(self.drawdown_pct, 4),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class PromotionDecision:
    """Decisión final del Promotion Manager (siempre registrada)."""

    genome_id: str
    name: str
    approved: bool
    operator: str
    drift: float
    improvement: float
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    decided_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "genome_id": self.genome_id,
            "name": self.name,
            "approved": self.approved,
            "operator": self.operator,
            "drift": round(self.drift, 4),
            "improvement": round(self.improvement, 4),
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "decided_at": isoformat_utc(self.decided_at),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ExperimentRecord:
    """Un experimento del Experiment Manager (append-only)."""

    id: str
    label: str
    kind: str
    status: ExperimentStatus
    hypothesis: Hypothesis | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    conclusions: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "status": self.status.value,
            "hypothesis": self.hypothesis.to_dict() if self.hypothesis else None,
            "payload": self.payload,
            "conclusions": self.conclusions,
            "created_at": isoformat_utc(self.created_at),
        }


# ---------------------------------------------------------------------------
# Reconstrucción desde su forma serializada (para stores append-only)
# ---------------------------------------------------------------------------


def genome_from_dict(data: dict[str, Any]) -> StrategyGenome:
    """Rebuild a :class:`StrategyGenome` from its serialized form."""
    blocks = tuple(
        SignalBlock(kind=b["kind"], params={k: float(v) for k, v in b.get("params", {}).items()})
        for b in data.get("blocks", [])
    )
    filters = tuple(
        ContextFilter(kind=f["kind"], params={k: float(v) for k, v in f.get("params", {}).items()})
        for f in data.get("filters", [])
    )
    return StrategyGenome(
        id=data["id"],
        name=data["name"],
        symbol=data["symbol"],
        timeframe=data["timeframe"],
        blocks=blocks,
        filters=filters,
        combine=Combine(data.get("combine", "all")),
        allow_short=bool(data.get("allow_short", True)),
        seed=int(data.get("seed", 0)),
        metadata=dict(data.get("metadata", {})),
    )


def candidate_report_from_dict(data: dict[str, Any]) -> CandidateReport:
    """Rebuild a :class:`CandidateReport` from its serialized form."""
    stages = tuple(
        StageResult(
            stage=PipelineStage(s["stage"]),
            passed=bool(s["passed"]),
            score=s.get("score"),
            detail=s.get("detail", ""),
            metrics={k: float(v) for k, v in s.get("metrics", {}).items()},
        )
        for s in data.get("stages", [])
    )
    return CandidateReport(
        genome_id=data["genome_id"],
        name=data["name"],
        symbol=data["symbol"],
        status=CandidateStatus(data["status"]),
        passed=bool(data["passed"]),
        objective=data["objective"],
        score=data.get("score"),
        stages=stages,
        statistics={k: float(v) for k, v in data.get("statistics", {}).items()},
        reasons=tuple(data.get("reasons", [])),
        created_at=datetime.fromisoformat(data["created_at"]),
    )

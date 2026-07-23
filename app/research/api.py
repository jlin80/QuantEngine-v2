"""ResearchLab: fachada del Quant Research Lab (Fase 10).

Punto único de entrada para el dashboard, el scheduler y las fases futuras.
Expone las funciones que pide la especificación —``create_experiment``,
``generate_strategy``, ``optimize_strategy``, ``validate_candidate``,
``promote_strategy``, ``reject_strategy``, ``rank_strategies``,
``generate_feature``, ``generate_report``, ``archive_experiment``— sobre los
motores desacoplados de este paquete. Refleja el patrón de ``QuantCore``,
``ExecutionCore``, ``BacktestLab`` y ``MLEngine``.

Regla de oro: el laboratorio **no opera**. Es independiente de producción,
trabaja siempre sobre copias de las estrategias y **nunca** habilita live
trading; la promoción final siempre exige aprobación humana.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.backtesting import BacktestLab
from app.backtesting.models import BacktestConfig
from app.config.settings import Settings
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.documentation.service import DocumentationService
from app.market.models import Candle
from app.research import events as ev
from app.research import stats as st
from app.research.bayesian_lab import BayesianLab, BayesianResult
from app.research.experiment_manager import ExperimentManager
from app.research.factor_lab import FactorLab
from app.research.feature_lab import FeatureLab
from app.research.genetic_optimizer import MultiObjectiveOptimizer, MultiObjectiveResult
from app.research.knowledge_base import KnowledgeBase
from app.research.models import (
    CandidateReport,
    CandidateStatus,
    ExperimentRecord,
    FactorReport,
    FeatureReport,
    Hypothesis,
    PaperTrialStatus,
    PromotionDecision,
    RankingEntry,
    ShadowComparison,
    StrategyGenome,
)
from app.research.paper_validation import PaperValidationTracker
from app.research.parameter_lab import build_source_factory, build_space
from app.research.production_candidate import CandidateStore, ProductionCandidate, PromotionManager
from app.research.ranking_engine import RankingEngine
from app.research.report_generator import ResearchReporter
from app.research.shadow_mode import ShadowComparator, ShadowSession
from app.research.simulation_cluster import SimulationCluster
from app.research.strategy_generator import StrategyGenerator, compile_genome
from app.research.strategy_generator.blocks import SIGNAL_BLOCKS
from app.research.validation_pipeline import CandidatePipeline
from app.research.validation_pipeline.pipeline import MLReviewFn

FeatureFn = Callable[[Sequence[Candle]], list[float]]


class ResearchLab:
    """Facade over the Quant Research Lab.

    Args:
        settings: Configuración raíz (usa ``research`` y ``backtesting``).
        bus: Event Bus para publicar hitos (opcional).
        lab: Laboratorio de backtesting a reutilizar (si no, se construye).
        documentation: Servicio de documentación (Notion) opcional.
        ml_review: Revisor ML asesor opcional para el pipeline.
        persist: Si los stores escriben a disco.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        bus: EventBus | None = None,
        lab: BacktestLab | None = None,
        documentation: DocumentationService | None = None,
        ml_review: MLReviewFn | None = None,
        persist: bool = True,
    ) -> None:
        self._settings = settings
        self._research = settings.research
        self._bus = bus
        self._doc = documentation
        self._log = logging.getLogger("app.research")
        self._lab = lab if lab is not None else BacktestLab(settings)
        persist = persist and self._research.persist

        self._generator = StrategyGenerator(self._research.generator)
        self._feature_lab = FeatureLab(self._research.feature_lab)
        self._factor_lab = FactorLab(self._research.factor_lab)
        self._multi = MultiObjectiveOptimizer(self._research.multi_objective)
        self._bayesian = BayesianLab(
            self._research.bayesian,
            history_dir=self._research.bayesian_dir if persist else None,
        )
        self._cluster = SimulationCluster(self._research.simulation)
        self._pipeline = CandidatePipeline(
            self._lab,
            self._research.pipeline,
            settings.backtesting.criteria,
            objective=self._research.objective,
            ml_review=ml_review,
        )
        self._ranking = RankingEngine(self._research.multi_objective.objectives)
        self._experiments = ExperimentManager(self._research.experiments_dir if persist else None)
        self._knowledge = KnowledgeBase(self._research.knowledge_dir if persist else None)
        self._candidates = CandidateStore(self._research.candidates_dir if persist else None)
        self._paper = PaperValidationTracker(self._research.paper)
        self._promotion = PromotionManager(
            self._research.promotion, self._research.multi_objective.objectives
        )
        self._shadow = ShadowComparator(self._lab, self._research.shadow)
        self._reporter = ResearchReporter()
        # Referencias fuertes a las publicaciones en vuelo (evita que el GC
        # cancele tareas no esperadas; se descartan al completarse).
        self._pending: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Componentes (para wiring/tests)
    # ------------------------------------------------------------------

    @property
    def backtesting(self) -> BacktestLab:
        """The underlying backtesting laboratory."""
        return self._lab

    @property
    def experiments(self) -> ExperimentManager:
        """The experiment manager."""
        return self._experiments

    @property
    def knowledge(self) -> KnowledgeBase:
        """The knowledge base."""
        return self._knowledge

    @property
    def candidates(self) -> CandidateStore:
        """The production-candidate store."""
        return self._candidates

    @property
    def paper(self) -> PaperValidationTracker:
        """The paper-validation tracker."""
        return self._paper

    @property
    def bayesian(self) -> BayesianLab:
        """The Bayesian optimization lab."""
        return self._bayesian

    @property
    def reporter(self) -> ResearchReporter:
        """The report generator."""
        return self._reporter

    @property
    def cluster(self) -> SimulationCluster:
        """The simulation cluster."""
        return self._cluster

    def make_config(self, symbol: str, **overrides: Any) -> BacktestConfig:
        """Build a backtest config from lab defaults (delegates to BacktestLab)."""
        return self._lab.make_config(symbol, **overrides)

    # ------------------------------------------------------------------
    # Generación de estrategias
    # ------------------------------------------------------------------

    def generate_strategy(
        self, symbol: str, timeframe: str, *, count: int | None = None, seed: int | None = None
    ) -> list[StrategyGenome]:
        """Generate a batch of experimental strategies (rule-based)."""
        genomes = self._generator.generate(symbol, timeframe, count=count, seed=seed)
        self._publish(
            ev.StrategyGenerated(
                source="research",
                count=len(genomes),
                symbol=symbol.upper(),
                sample_name=genomes[0].name if genomes else "",
            )
        )
        return genomes

    def catalog(self) -> dict[str, Any]:
        """Catalog of building blocks, features and factors (for the dashboard)."""
        return {
            "generator": self._generator.catalog(),
            "features": self._feature_lab.catalog(),
            "factors": self._factor_lab.catalog(),
        }

    # ------------------------------------------------------------------
    # Optimización
    # ------------------------------------------------------------------

    def optimize_strategy(
        self,
        genome: StrategyGenome,
        candles: Sequence[Candle],
        config: BacktestConfig,
        *,
        method: str = "multi",
    ) -> MultiObjectiveResult | BayesianResult:
        """Optimize a genome's parameters (multi-objective or Bayesian).

        Args:
            genome: Genoma a optimizar (nunca se muta: se clona por evaluación).
            candles: Serie histórica.
            config: Configuración base del backtest.
            method: ``multi`` (genético multiobjetivo) o ``bayesian`` (TPE).

        Returns:
            El resultado de la optimización.

        Raises:
            ValueError: Si el método es desconocido.
        """
        space = build_space(genome)
        if method == "multi":
            result: MultiObjectiveResult | BayesianResult = self._multi.optimize(
                space, self._metrics_fn(genome, candles, config)
            )
            best_score = result.best_score
        elif method == "bayesian":
            result = self._bayesian.optimize(
                space,
                self._objective_fn(genome, candles, config),
                label=genome.name,
                objective=self._research.objective,
                context=f"{genome.symbol} {genome.id}",
            )
            best_score = result.best_score
        else:
            raise ValueError(f"Método de optimización desconocido: {method}")
        self._publish(
            ev.OptimizationCompleted(
                source="research",
                method=result.method,
                objective=self._research.objective,
                evaluations=result.evaluations,
                best_score=best_score if best_score != float("-inf") else None,
            )
        )
        return result

    # ------------------------------------------------------------------
    # Validación de candidatas
    # ------------------------------------------------------------------

    def validate_candidate(
        self,
        genome: StrategyGenome,
        candles: Sequence[Candle],
        config: BacktestConfig,
        *,
        register: bool = True,
    ) -> CandidateReport:
        """Run a genome through the full candidate pipeline.

        Args:
            genome: Genoma a validar.
            candles: Serie histórica.
            config: Configuración base del backtest.
            register: Si registra la candidata y el conocimiento.

        Returns:
            El informe de candidata (aprobada o rechazada, con etapas).
        """
        report = self._pipeline.evaluate(genome, candles, config)
        if register:
            self._knowledge.record_candidate(report, genome)
            if report.passed:
                self._candidates.register(genome, report)
        if report.passed:
            self._publish(
                ev.CandidateQualified(
                    source="research",
                    genome_id=genome.id,
                    name=genome.name,
                    symbol=genome.symbol,
                    objective=report.objective,
                    score=report.score,
                    metrics=dict(report.statistics),
                )
            )
        else:
            self._publish(
                ev.CandidateFailed(
                    source="research",
                    genome_id=genome.id,
                    name=genome.name,
                    reasons=report.reasons,
                )
            )
        return report

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def rank_strategies(
        self, reports: Sequence[CandidateReport] | None = None, *, segment: str = "global"
    ) -> list[RankingEntry]:
        """Rank candidate reports (defaults to the registered candidates)."""
        if reports is None:
            reports = [candidate.report for candidate in self._candidates.list()]
        entries = self._ranking.rank(reports, segment=segment)
        self._publish(
            ev.RankingUpdated(
                source="research",
                segment=segment,
                size=len(entries),
                leader=entries[0].name if entries else "",
            )
        )
        return entries

    def rank_by_symbol(self) -> dict[str, list[RankingEntry]]:
        """Rank the registered candidates grouped by symbol."""
        return self._ranking.by_symbol([c.report for c in self._candidates.list()])

    # ------------------------------------------------------------------
    # Feature Lab / Factor Lab
    # ------------------------------------------------------------------

    def generate_feature(
        self, name: str, fn: FeatureFn, candles: Sequence[Candle]
    ) -> FeatureReport:
        """Register and validate a new candidate feature."""
        self._feature_lab.register(name, fn)
        report = self._feature_lab.validate(name, candles)
        self._publish(
            ev.FeatureValidated(source="research", feature=name, valid=report.valid, ic=report.ic)
        )
        return report

    def validate_features(self, candles: Sequence[Candle]) -> list[FeatureReport]:
        """Validate every registered candidate feature (ranked by |IC|)."""
        return self._feature_lab.validate_all(candles)

    def research_factors(self, candles: Sequence[Candle]) -> list[FactorReport]:
        """Research and rank every factor family."""
        ranked = self._factor_lab.research(candles)
        retained = [r for r in ranked if abs(r.ic) >= self._research.factor_lab.min_abs_ic]
        self._publish(
            ev.FactorResearchCompleted(
                source="research",
                tested=len(ranked),
                retained=len(retained),
                top_factor=ranked[0].name if ranked else "",
            )
        )
        return ranked

    # ------------------------------------------------------------------
    # Shadow Mode
    # ------------------------------------------------------------------

    def compare_shadow(
        self,
        official: StrategyGenome,
        challenger: StrategyGenome,
        candles: Sequence[Candle],
        config: BacktestConfig,
    ) -> ShadowComparison:
        """Compare a challenger genome against the official over identical data."""
        comparison = self._shadow.compare(
            compile_genome(official),
            compile_genome(challenger),
            candles,
            config,
            official_name=official.name,
            challenger_name=challenger.name,
        )
        self._publish(
            ev.ShadowReportReady(
                source="research",
                official=comparison.official,
                challenger=comparison.challenger,
                better=comparison.better,
                significant=comparison.significant,
                p_value=comparison.p_value,
                effect_r=comparison.effect_r,
            )
        )
        return comparison

    def open_shadow_session(
        self,
        official: StrategyGenome,
        challenger: StrategyGenome,
        config: BacktestConfig,
    ) -> ShadowSession:
        """Open a streaming shadow session (parallel, order-free)."""
        return ShadowSession(
            self._shadow,
            compile_genome(official),
            compile_genome(challenger),
            config,
            self._research.shadow,
            official_name=official.name,
            challenger_name=challenger.name,
        )

    # ------------------------------------------------------------------
    # Paper validation
    # ------------------------------------------------------------------

    def start_paper(self, genome_id: str) -> None:
        """Open a paper-validation trial for a candidate."""
        self._paper.open(genome_id)

    def update_paper(self, genome_id: str, statistics: dict[str, float]) -> PaperTrialStatus:
        """Update a paper trial from statistics and return its maturity."""
        self._paper.update_from_stats(genome_id, statistics)
        status = self._paper.status(genome_id)
        if self._candidates.count() and genome_id in {c.genome.id for c in self._candidates.list()}:
            self._candidates.attach_paper(genome_id, status)
        return status

    def paper_status(self, genome_id: str) -> PaperTrialStatus:
        """Current maturity status of a paper trial."""
        return self._paper.status(genome_id)

    # ------------------------------------------------------------------
    # Reportes
    # ------------------------------------------------------------------

    def generate_report(self, kind: str, **kwargs: Any) -> dict[str, Any]:
        """Build a research report (``candidate`` | ``ranking`` | ``shadow``).

        Raises:
            ValueError: Si el tipo de informe es desconocido.
        """
        if kind == "candidate":
            return self._reporter.candidate_report(kwargs["report"])
        if kind == "ranking":
            return self._reporter.ranking_report(
                kwargs["entries"], segment=kwargs.get("segment", "global")
            )
        if kind == "shadow":
            return self._reporter.shadow_report(kwargs["comparison"])
        raise ValueError(f"Tipo de informe desconocido: {kind}")

    # ------------------------------------------------------------------
    # Experimentos (async: publican eventos y documentan en Notion)
    # ------------------------------------------------------------------

    async def create_experiment(
        self,
        label: str,
        kind: str,
        *,
        hypothesis: Hypothesis | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ExperimentRecord:
        """Register a new experiment (publishes an event and documents it)."""
        record = self._experiments.create(label, kind, hypothesis=hypothesis, payload=payload)
        self._publish(
            ev.ExperimentCreated(
                source="research",
                experiment_id=record.id,
                label=label,
                kind=kind,
                hypothesis=hypothesis.text if hypothesis else "",
            )
        )
        await self._document(
            f"Experimento: {label}",
            self._experiment_markdown(record),
            tags=("research", "experiment", kind),
        )
        return record

    async def archive_experiment(
        self, experiment_id: str, conclusions: str = ""
    ) -> ExperimentRecord:
        """Archive an experiment (knowledge retained, publishes an event)."""
        record = self._experiments.archive(experiment_id, conclusions)
        self._publish(
            ev.ExperimentArchived(
                source="research",
                experiment_id=record.id,
                label=record.label,
                conclusions=record.conclusions,
            )
        )
        return record

    # ------------------------------------------------------------------
    # Promoción / rechazo (fail-closed; nunca habilita live)
    # ------------------------------------------------------------------

    async def promote_strategy(
        self,
        genome_id: str,
        *,
        operator: str,
        operator_approved: bool,
        current_metrics: Mapping[str, float] | None = None,
        drift: float = 0.0,
    ) -> PromotionDecision:
        """Evaluate and record a promotion decision (never enables live).

        Args:
            genome_id: Candidata a promover.
            operator: Actor de la decisión (auditoría).
            operator_approved: Aprobación explícita del operador.
            current_metrics: Métricas de la estrategia vigente (o ``None``).
            drift: Deriva medida de las features.

        Returns:
            La decisión de promoción, siempre registrada y explicada.
        """
        candidate = self._candidates.get(genome_id)
        paper_status = candidate.paper
        decision = self._promotion.evaluate(
            candidate.genome,
            candidate.report,
            paper_status=paper_status,
            current_metrics=current_metrics,
            drift=drift,
            operator_approved=operator_approved,
            operator=operator,
        )
        self._candidates.attach_promotion(genome_id, decision)
        if decision.approved:
            self._publish(
                ev.StrategyPromoted(
                    source="research",
                    genome_id=genome_id,
                    name=candidate.genome.name,
                    operator=operator,
                    improvement=decision.improvement,
                )
            )
            await self._document(
                f"Promoción: {candidate.genome.name}",
                self._promotion_markdown(decision),
                tags=("research", "promotion"),
            )
        else:
            self._publish(
                ev.StrategyRejected(
                    source="research",
                    genome_id=genome_id,
                    name=candidate.genome.name,
                    reasons=decision.blockers,
                )
            )
        return decision

    async def reject_strategy(self, genome_id: str, reason: str) -> ProductionCandidate:
        """Reject a candidate explicitly (publishes an event)."""
        candidate = self._candidates.set_status(genome_id, CandidateStatus.REJECTED)
        self._publish(
            ev.StrategyRejected(
                source="research",
                genome_id=genome_id,
                name=candidate.genome.name,
                reasons=(reason,),
            )
        )
        return candidate

    # ------------------------------------------------------------------
    # Orquestación (async): ciclo completo de investigación
    # ------------------------------------------------------------------

    async def run_generation_cycle(
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[Candle],
        config: BacktestConfig,
        *,
        count: int | None = None,
    ) -> dict[str, Any]:
        """Generate, validate (in the cluster), rank and record a batch.

        No opera ni promueve: descubre candidatas y las deja listas para la
        validación paper y la decisión humana.
        """
        genomes = self.generate_strategy(symbol, timeframe, count=count)
        jobs = [self._pipeline_job(genome, candles, config) for genome in genomes]
        reports = await self._cluster.run(jobs)
        qualified: list[CandidateReport] = []
        for genome, report in zip(genomes, reports, strict=True):
            self._knowledge.record_candidate(report, genome)
            if report.passed:
                self._candidates.register(genome, report)
                qualified.append(report)
                self._publish(
                    ev.CandidateQualified(
                        source="research",
                        genome_id=genome.id,
                        name=genome.name,
                        symbol=genome.symbol,
                        objective=report.objective,
                        score=report.score,
                        metrics=dict(report.statistics),
                    )
                )
        entries = self.rank_strategies(qualified, segment=symbol.upper()) if qualified else []
        return {
            "symbol": symbol.upper(),
            "generated": len(genomes),
            "qualified": len(qualified),
            "ranking": [entry.to_dict() for entry in entries[:10]],
        }

    # ------------------------------------------------------------------
    # Diagnóstico (dashboard)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Compact laboratory status for the dashboard."""
        return {
            "enabled": self._research.enabled,
            "objective": self._research.objective,
            "objectives": self._research.multi_objective.objectives,
            "experiments": self._experiments.count(),
            "candidates": self._candidates.count(),
            "knowledge_entries": self._knowledge.count(),
            "bayesian_runs": self._bayesian.history.count(),
            "paper_trials": len(self._paper.active()),
            "blocks": len(SIGNAL_BLOCKS),
        }

    def report(self) -> dict[str, Any]:
        """Full observation report for the dashboard."""
        return {
            "status": self.status(),
            "candidates": [c.to_dict() for c in self._candidates.list()],
            "experiments": [e.to_dict() for e in self._experiments.list()],
            "knowledge": self._knowledge.summary(),
            "bayesian": self._bayesian.history.compare(),
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _metrics_fn(
        self, genome: StrategyGenome, candles: Sequence[Candle], config: BacktestConfig
    ) -> Callable[[dict[str, Any]], dict[str, float]]:
        """Backtest-based metrics function for the multi-objective optimizer."""
        factory = build_source_factory(genome)

        def metrics(params: dict[str, Any]) -> dict[str, float]:
            source = factory(params)
            source.reset()
            result = self._lab.run_backtest(candles, source, config)
            stats = {
                k: float(v) for k, v in result.statistics.items() if isinstance(v, (int, float))
            }
            stats["stability"] = round(
                st.equity_stability([p.equity for p in result.equity_curve]), 6
            )
            return stats

        return metrics

    def _objective_fn(
        self, genome: StrategyGenome, candles: Sequence[Candle], config: BacktestConfig
    ) -> Callable[[dict[str, Any]], float]:
        """Single-objective function (primary metric) for the Bayesian lab."""
        metrics = self._metrics_fn(genome, candles, config)
        objective = self._research.objective

        def evaluate(params: dict[str, Any]) -> float:
            return metrics(params).get(objective, float("-inf"))

        return evaluate

    def _pipeline_job(
        self, genome: StrategyGenome, candles: Sequence[Candle], config: BacktestConfig
    ) -> Callable[[], CandidateReport]:
        """Bind a genome into a zero-argument evaluation job (for the cluster)."""

        def job() -> CandidateReport:
            return self._pipeline.evaluate(genome, candles, config)

        return job

    def _publish(self, event: Event) -> None:
        """Best-effort event publish scheduled on the running loop (if any)."""
        if self._bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # contexto síncrono/sin loop (tests): se omite la publicación
        task = loop.create_task(self._safe_publish(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _safe_publish(self, event: Event) -> None:
        """Publish swallowing any bus error (the lab never breaks on telemetry)."""
        try:
            await self._bus.publish(event)  # type: ignore[union-attr]
        except Exception as exc:
            self._log.debug("No se pudo publicar %s: %r", event.name, exc)

    async def _document(self, title: str, content: str, *, tags: tuple[str, ...]) -> None:
        """Record a journal entry (Notion/Markdown) best-effort."""
        if self._doc is None:
            return
        try:
            await self._doc.record_decision(title, content, tags=tags)
        except Exception as exc:
            self._log.debug("No se pudo documentar '%s': %r", title, exc)

    @staticmethod
    def _experiment_markdown(record: ExperimentRecord) -> str:
        """Markdown body for an experiment journal entry."""
        hyp = record.hypothesis.text if record.hypothesis else "—"
        return (
            f"**Tipo:** {record.kind}\n\n"
            f"**Hipótesis:** {hyp}\n\n"
            f"**Estado:** {record.status.value}\n\n"
            f"ID: `{record.id}`"
        )

    @staticmethod
    def _promotion_markdown(decision: PromotionDecision) -> str:
        """Markdown body for a promotion journal entry."""
        reasons = "\n".join(f"- {r}" for r in decision.reasons) or "—"
        return (
            f"**Aprobada:** {'sí' if decision.approved else 'no'}\n\n"
            f"**Operador:** {decision.operator}\n\n"
            f"**Mejora:** {decision.improvement:+.3f}\n\n"
            f"**Motivos:**\n{reasons}"
        )

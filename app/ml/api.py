"""MLEngine: fachada de las APIs internas del Machine Learning (Fase 7).

Punto único de entrada para el dashboard, el scheduler y las fases futuras.
Expone las funciones que pide la especificación —``train_model``,
``evaluate_model``, ``predict_trade_quality``, ``calculate_strategy_weight``,
``detect_drift``, ``rank_strategies``, ``recommend_parameters``,
``explain_prediction``, ``register_model``, ``activate_model``,
``rollback_model``, ``run_auto_ml``— sobre los motores desacoplados de este
paquete. Refleja el patrón de ``QuantCore``, ``ExecutionCore`` y ``BacktestLab``.

Regla absoluta: el ML **asesora, no decide**. Ninguna función abre operaciones
ni habilita live trading; toda recomendación pasa por el Decision Engine y el
Risk Manager. El sistema sigue operando sólo en paper trading.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.config.settings import Settings
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.exceptions import InsufficientDataError, MLError
from app.execution.models.trades import TradeRecord
from app.ml.auto_ml import AutoML, AutoMLResult
from app.ml.datasets import (
    SIGNAL_LABEL,
    Dataset,
    DatasetBuilder,
    SignalOutcome,
    era_summary,
)
from app.ml.drift import DriftDetector, DriftReport
from app.ml.events import (
    DriftDetected,
    MetaStrategyDecision,
    ModelActivated,
    ModelApproved,
    ModelRejected,
    ModelRequiresRetraining,
    ModelTrainingFailed,
    ModelTrainingFinished,
    ModelTrainingStarted,
    StrategyWeightsUpdated,
    to_metric_dict,
)
from app.ml.experiments import MLExperimentTracker
from app.ml.feature_store import FeatureStore
from app.ml.features import FeatureEngineer
from app.ml.inference import InferenceService
from app.ml.meta import MetaReport, MetaStrategyManager
from app.ml.models.factory import build_model
from app.ml.monitoring import ModelPerformanceMonitor
from app.ml.monitoring.execution_rules import (
    ExecutionRulesCheck,
    changed_rule_groups,
    execution_rules_hash,
    execution_rules_snapshot,
)
from app.ml.optimization import ParameterRecommender
from app.ml.prediction import Prediction
from app.ml.registry import ModelRecord, ModelRegistry
from app.ml.reporting import MLReporter
from app.ml.services import (
    AIAdvisor,
    RiskAdvisor,
    StrategyIntelligence,
    StrategyScore,
    VirtualStrategyStats,
)
from app.ml.training import Trainer, TrainingResult

TradesProvider = Callable[[], Sequence[TradeRecord]]
"""Devuelve el historial de operaciones (normalmente el Trade Journal)."""

VirtualStatsProvider = Callable[[], Mapping[str, VirtualStrategyStats]]
"""Rendimiento por estrategia del evaluador continuo (Fase 4).

Se inyecta como callable para que la capa de ML no dependa del motor de
estrategias: el composition root adapta el ``PerformanceTracker``.
"""

SignalOutcomesProvider = Callable[[], Mapping[str, SignalOutcome]]
"""Resultado virtual **por señal** del evaluador continuo (Bloque 8).

El anterior devuelve el agregado por estrategia; éste devuelve la fila, que es
lo que permite unir cada operación con la resolución de su propia señal. Mismo
motivo para el callable: el composition root adapta el ``VirtualOutcomeStore``.
"""


class MLEngine:
    """Facade over the Machine Learning layer.

    Args:
        settings: Configuración raíz (usa ``ml`` y ``execution``).
        bus: Event Bus para publicar hitos (opcional).
        trades_provider: Fuente del historial de operaciones (journal, Fase 5)
            — mide lo que la ejecución **capturó**.
        virtual_stats_provider: Fuente de las métricas por estrategia del
            evaluador continuo (Fase 4) — mide la calidad de la señal *en sí*.
            El Meta Strategy Manager las usa como prior mientras la muestra
            ejecutada de una estrategia sea escasa.
        signal_outcomes_provider: Fuente del resultado virtual por ``signal_id``
            (Bloque 8). Con ella, la etiqueta de calidad de señal sale del join
            real contra el evaluador; sin ella, de la aproximación por motivo de
            salida heredada del Bloque 4.
        persist: Si el registro y los experimentos escriben a disco.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        bus: EventBus | None = None,
        trades_provider: TradesProvider | None = None,
        virtual_stats_provider: VirtualStatsProvider | None = None,
        signal_outcomes_provider: SignalOutcomesProvider | None = None,
        persist: bool = True,
    ) -> None:
        self._settings = settings
        self._ml = settings.ml
        self._bus = bus
        self._trades_provider: TradesProvider = trades_provider or (lambda: [])
        self._virtual_stats_provider: VirtualStatsProvider = virtual_stats_provider or (lambda: {})
        self._signal_outcomes_provider: SignalOutcomesProvider = signal_outcomes_provider or (
            lambda: {}
        )
        self._log = logging.getLogger("app.ml")

        self._engineer = FeatureEngineer()
        self._feature_store = FeatureStore()
        self._feature_store.register_engineer(self._engineer)
        self._builder = DatasetBuilder(self._engineer, self._ml.data_quality)
        self._trainer = Trainer(self._ml.training)
        self._registry = ModelRegistry(self._ml.registry_dir if persist else None)
        self._automl = AutoML(self._trainer, self._ml.model, seed=self._ml.training.random_seed)
        self._drift = DriftDetector(self._ml.drift)
        self._monitor = ModelPerformanceMonitor()
        self._inference = InferenceService(self._registry, self._engineer)
        self._intelligence = StrategyIntelligence(lookback=self._ml.meta.lookback_trades)
        self._meta = MetaStrategyManager(self._ml.meta, self._intelligence)
        self._recommender = ParameterRecommender(min_trades=self._ml.advisor.min_similar_trades)
        self._risk_advisor = RiskAdvisor(self._inference, self._ml.advisor)
        self._advisor = AIAdvisor(self._risk_advisor, self._recommender)
        self._experiments = MLExperimentTracker(self._ml.experiments_dir if persist else None)
        self._reporter = MLReporter()
        self._reference_dataset: Dataset | None = None
        self._last_drift: DriftReport | None = None
        # Latch del aviso de reglas de ejecución (ver
        # `run_execution_rules_check`): evita repetir el mismo aviso cada hora.
        self._rules_alert_signature: tuple[str, str, str] | None = None

    # ------------------------------------------------------------------
    # Componentes (para wiring/tests)
    # ------------------------------------------------------------------

    @property
    def registry(self) -> ModelRegistry:
        """The model registry."""
        return self._registry

    @property
    def feature_store(self) -> FeatureStore:
        """The professional feature store."""
        return self._feature_store

    @property
    def inference(self) -> InferenceService:
        """The inference service (active model)."""
        return self._inference

    @property
    def meta(self) -> MetaStrategyManager:
        """The meta-strategy manager."""
        return self._meta

    @property
    def advisor(self) -> AIAdvisor:
        """The AI advisor."""
        return self._advisor

    @property
    def experiments(self) -> MLExperimentTracker:
        """The experiment tracker."""
        return self._experiments

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------

    def build_dataset(self, *, label: str = "win") -> Dataset:
        """Build a training dataset from the journalled trades.

        El dataset ya viene saneado por era de ejecución: las operaciones de
        eras con bugs conocidos pesan menos o quedan fuera, para que el modelo
        no aprenda "esta señal es mala" cuando lo cierto es "la ejecución la
        saboteó". La procedencia completa va en ``dataset.metadata``.

        Args:
            label: ``win`` (calidad de ejecución, por defecto) o
                ``signal_quality`` (calidad de la señal en sí).
        """
        return self._builder.build(
            self._trades_provider(),
            label=label,
            outcomes=self._signal_outcomes_provider(),
        )

    def build_signal_dataset(self) -> Dataset:
        """Dataset etiquetado por **calidad de señal**, no de ejecución.

        Desde el Bloque 8 la etiqueta sale del **join real** con el evaluador
        continuo: para cada operación, el resultado virtual de sus señales,
        medido sobre precio posterior a la señal. Eso incluye las operaciones
        que la ejecución cortó por régimen o por tiempo — precisamente las que
        la aproximación anterior tenía que descartar por no poder juzgarlas.

        Para el historial sin ``signal_ids`` (todo el journal anterior al
        bloque) se mantiene esa aproximación: sólo cuentan las operaciones cuyo
        cierre resolvió la tesis. ``metadata["join_breakdown"]`` dice cuántas
        filas vinieron de cada camino.

        Es la contraparte del dataset por defecto, no su sustituto — juntos
        separan "¿esta estrategia tiene edge?" de "¿la ejecución lo captura?".
        """
        return self._builder.build(
            self._trades_provider(),
            label=SIGNAL_LABEL,
            outcomes=self._signal_outcomes_provider(),
        )

    def data_quality_report(self) -> dict[str, Any]:
        """Qué datos entran al entrenamiento y por qué (auditoría del Bloque 4)."""
        trades = list(self._trades_provider())
        return {
            "settings": self._ml.data_quality.model_dump(mode="json"),
            "breakdown": era_summary(trades, self._ml.data_quality),
        }

    def labeled_trades(self) -> list[tuple[str, TradeRecord]]:
        """The journalled trades tagged with their originating strategy."""
        return StrategyIntelligence.label_trades(list(self._trades_provider()))

    # ------------------------------------------------------------------
    # Entrenamiento, validación y registro
    # ------------------------------------------------------------------

    def train_model(
        self, model_type: str | None = None, dataset: Dataset | None = None
    ) -> TrainingResult:
        """Train a model with temporal validation (does not register)."""
        data = dataset if dataset is not None else self.build_dataset()
        kind = model_type or self._ml.model.default_model
        result = self._trainer.train(
            lambda: build_model(kind, self._ml.model, seed=self._ml.training.random_seed), data
        )
        self._reference_dataset = data
        return result

    def run_auto_ml(
        self, dataset: Dataset | None = None, model_types: list[str] | None = None
    ) -> AutoMLResult:
        """Search models and hyperparameters, returning a ranked leaderboard."""
        data = dataset if dataset is not None else self.build_dataset()
        self._reference_dataset = data
        return self._automl.run(data, model_types or list(self._ml.automl_models))

    def evaluate_model(self, result: TrainingResult) -> tuple[bool, list[str]]:
        """Validation gate: decide whether a trained model may be approved.

        Nunca aprueba un modelo inferior al activo (si se exige batirlo). Un
        modelo que no supera los mínimos se rechaza con motivos explícitos.
        """
        v = self._ml.validation
        # El AUC de referencia es el del HOLDOUT. Antes se usaba
        # `max(holdout, cv)`, que descartaba la validación cruzada justo cuando
        # contradecía al holdout: un modelo con holdout 0.663 y cv 0.463 (peor
        # que el azar) se aprobaba con "supera los mínimos (AUC 0.663)".
        auc = result.holdout.auc
        cv_auc = result.cross_validation.mean_auc
        reasons: list[str] = []
        approved = True
        if result.dataset_summary.get("samples", 0) < v.min_samples:
            approved = False
            reasons.append(f"dataset insuficiente (<{v.min_samples})")
        if auc < v.min_auc:
            approved = False
            reasons.append(f"AUC {auc:.3f} < mínimo {v.min_auc}")
        # Puerta anti-sobreajuste: la CV se evalúa por separado, nunca se
        # compensa con un holdout optimista.
        if cv_auc < v.min_cv_auc:
            approved = False
            reasons.append(
                f"AUC de validación cruzada {cv_auc:.3f} < mínimo {v.min_cv_auc} "
                "(sobreajuste: el holdout no basta)"
            )
        if result.holdout.accuracy < v.min_accuracy:
            approved = False
            reasons.append(f"accuracy {result.holdout.accuracy:.3f} < mínimo {v.min_accuracy}")
        if v.require_beat_previous:
            active = self._registry.active_record()
            if active is not None:
                previous = float(active.metrics.get("auc", 0.0))
                if auc < previous + v.min_improvement:
                    approved = False
                    reasons.append(f"no bate al modelo activo (AUC {auc:.3f} vs {previous:.3f})")
        if approved and not reasons:
            reasons.append(f"supera los mínimos (AUC {auc:.3f}, CV {cv_auc:.3f})")
        return approved, reasons

    def register_model(
        self, result: TrainingResult, *, author: str = "system", result_note: str = ""
    ) -> ModelRecord:
        """Register a trained model version in the registry (never overwrites).

        Congela con el modelo la huella de las reglas de ejecución vigentes: sin
        ella no se puede detectar después que el modelo dejó de representar cómo
        opera el motor (sus métricas de validación no cambian por eso).
        """
        return self._registry.register(
            result.model,
            metrics=to_metric_dict(result.metrics()),
            dataset=result.dataset_summary,
            feature_names=result.feature_names,
            author=author,
            result=result_note,
            execution_rules_hash=execution_rules_hash(self._settings.execution),
            execution_rules=execution_rules_snapshot(self._settings.execution),
        )

    # ------------------------------------------------------------------
    # Vigencia del modelo frente a las reglas de ejecución (Bloque 5)
    # ------------------------------------------------------------------

    def check_execution_rules(self) -> ExecutionRulesCheck:
        """Compare the active model against the execution rules in force.

        Segunda capa sobre el gate de validación: aquel evita activar un modelo
        malo, esto detecta que un modelo **bueno** dejó de ser representativo
        porque cambiaron las reglas bajo las que se entrenó (holding, sizing,
        filtros de riesgo, trailing).

        No desactiva nada ni dispara un reentrenamiento: sólo diagnostica. El ML
        asesora y nunca decide por sí solo.
        """
        current_snapshot = execution_rules_snapshot(self._settings.execution)
        current = execution_rules_hash(self._settings.execution)
        record = self._registry.active_record()
        if record is None:
            return ExecutionRulesCheck(
                status="no_model",
                current_hash=current,
                detail="No hay modelo activo que validar.",
            )
        if not record.execution_rules_hash:
            return ExecutionRulesCheck(
                status="unknown",
                current_hash=current,
                model_id=record.id,
                model_version=record.version,
                detail=(
                    "El modelo activo se registró antes de que se guardara la huella "
                    "de reglas de ejecución; no se puede afirmar que siga siendo "
                    "representativo. Se recomienda reentrenar para fijar la huella."
                ),
            )
        if record.execution_rules_hash == current:
            return ExecutionRulesCheck(
                status="ok",
                current_hash=current,
                model_hash=record.execution_rules_hash,
                model_id=record.id,
                model_version=record.version,
                detail="El modelo activo se entrenó con las reglas de ejecución vigentes.",
            )
        changed = changed_rule_groups(record.execution_rules, current_snapshot)
        return ExecutionRulesCheck(
            status="stale",
            current_hash=current,
            model_hash=record.execution_rules_hash,
            model_id=record.id,
            model_version=record.version,
            changed=changed,
            detail=(
                f"Las reglas de ejecución cambiaron ({', '.join(changed) or 'sin detalle'}) "
                f"desde que se entrenó el modelo activo v{record.version}. Sigue asesorando "
                f"con la estadística de un motor que ya no existe: **requiere "
                f"reentrenamiento**. No se ha desactivado nada."
            ),
        )

    async def run_execution_rules_check(self) -> dict[str, Any]:
        """Scheduled check; alerts on Discord when the active model is stale.

        Sólo alerta y sugiere reentrenar — nunca autoactiva ni desactiva.

        **Avisa una vez por situación, no una vez por vuelta.** El estado
        ``stale``/``unknown`` es persistente por naturaleza: dura hasta que un
        humano reentrena. Sin latch, un job horario convierte un aviso útil en
        ruido de fondo que se acaba silenciando — que es justo lo contrario de
        lo que se pretende. El latch se rearma cuando cambia la situación (otro
        modelo activo, otras reglas, o vuelta a ``ok``), de modo que un problema
        *nuevo* siempre vuelve a anunciarse.
        """
        if not self._ml.execution_rules_check.enabled:
            return {"status": "disabled", "needs_retraining": False}
        check = self.check_execution_rules()
        # Identidad de la situación, no del mensaje: mismo modelo + mismas
        # reglas + mismo estado = mismo aviso.
        signature = (check.status, check.model_id, check.current_hash)
        if check.status in ("stale", "unknown"):
            if signature != self._rules_alert_signature:
                self._rules_alert_signature = signature
                await self._publish(
                    ModelRequiresRetraining(
                        source="ml",
                        model_id=check.model_id,
                        model_version=check.model_version,
                        reason=check.status,
                        changed=tuple(check.changed),
                        detail=check.detail,
                    )
                )
        else:
            self._rules_alert_signature = None
        return check.to_dict()

    def activate_model(self, model_id: str) -> ModelRecord:
        """Make a registered model the active advisory model."""
        record = self._registry.activate(model_id)
        self._monitor.set_baseline(float(record.metrics.get("auc", 0.5)))
        return record

    def rollback_model(self) -> ModelRecord | None:
        """Roll back to the previously active model (immediate)."""
        return self._registry.rollback()

    # ------------------------------------------------------------------
    # Inferencia y explicabilidad
    # ------------------------------------------------------------------

    def predict_trade_quality(self, context: Mapping[str, Any]) -> Prediction:
        """Predict the quality of a candidate trade (explained; advisory only)."""
        return self._inference.predict(context)

    def explain_prediction(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Explain a candidate-trade prediction (never just a number)."""
        return self._inference.predict(context).to_dict()

    def assess_risk(
        self, context: Mapping[str, Any], *, symbol: str | None = None, regime: str | None = None
    ) -> dict[str, Any]:
        """Advisory risk estimate for a candidate trade (does not operate)."""
        similar = RiskAdvisor.filter_similar(
            list(self._trades_provider()), symbol=symbol, regime=regime
        )
        return self._risk_advisor.assess(context, similar).to_dict()

    def record_outcome(self, probability: float, outcome: int) -> None:
        """Feed a realised outcome to the live performance monitor."""
        self._monitor.record(probability, outcome)

    # ------------------------------------------------------------------
    # Deriva
    # ------------------------------------------------------------------

    def detect_drift(self, current: Dataset | None = None) -> DriftReport:
        """Detect feature/concept/performance/model drift and adjust confidence."""
        data = current if current is not None else self.build_dataset()
        reference = self._reference_dataset
        if reference is None or len(reference) == 0:
            reference, data = data.split(0.5)
        active = self._registry.active_record()
        baseline_auc = float(active.metrics.get("auc", 0.0)) if active else None
        live = self._monitor.metrics()
        live_auc = live.get("auc") if self._monitor.samples >= self._ml.drift.min_samples else None
        report = self._drift.analyze(reference, data, baseline_auc=baseline_auc, live_auc=live_auc)
        self._last_drift = report
        factor = report.confidence_factor(self._ml.drift.reduce_confidence_factor)
        self._inference.set_confidence_factor(factor)
        return report

    # ------------------------------------------------------------------
    # Estrategias (ranking y pesos dinámicos)
    # ------------------------------------------------------------------

    def rank_strategies(
        self, labeled: Sequence[tuple[str, TradeRecord]] | None = None
    ) -> list[StrategyScore]:
        """Rank strategies by recent/historical/segmented performance."""
        return self._intelligence.rank(labeled if labeled is not None else self.labeled_trades())

    def calculate_strategy_weight(self, strategy: str) -> float:
        """Current dynamic weight for a strategy (from meta governance)."""
        weights = self._meta.weights()
        if strategy in weights:
            return weights[strategy]
        for score in self.rank_strategies():
            if score.name == strategy:
                span = self._ml.meta.max_weight - self._ml.meta.min_weight
                return round(self._ml.meta.min_weight + span * (score.score / 100.0), 4)
        return 1.0

    def virtual_stats(self) -> Mapping[str, VirtualStrategyStats]:
        """Per-strategy virtual performance from the continuous evaluator."""
        return self._virtual_stats_provider()

    def evaluate_meta(self, labeled: Sequence[tuple[str, TradeRecord]] | None = None) -> MetaReport:
        """Run a meta-strategy governance cycle (adjusts weights/activation).

        Combina las dos fuentes de evidencia por estrategia: el Trade Journal
        (lo ejecutado) y el evaluador continuo (la señal en sí). La segunda sólo
        pesa mientras la primera no tenga muestra suficiente, y nunca puede por
        sí sola desactivar una estrategia.
        """
        return self._meta.evaluate(
            labeled if labeled is not None else self.labeled_trades(),
            self.virtual_stats(),
        )

    def recommend_parameters(self) -> dict[str, Any]:
        """Evidence-based parameter recommendations per strategy."""
        data: dict[str, Any] = self._advisor.recommend_parameters(self.rank_strategies())["data"]
        return data

    # ------------------------------------------------------------------
    # Orquestación asíncrona (usada por el scheduler; publica eventos)
    # ------------------------------------------------------------------

    async def run_nightly_training(self, *, author: str = "scheduler") -> dict[str, Any]:
        """Nightly AutoML → validate → register → (optionally) activate."""
        try:
            dataset = self.build_dataset()
        except MLError as exc:
            return {"status": "error", "detail": str(exc)}
        if len(dataset) < self._ml.training.min_samples:
            return {"status": "skipped", "reason": "datos insuficientes", "samples": len(dataset)}
        await self._publish(
            ModelTrainingStarted(source="ml", model_type="automl", dataset_size=len(dataset))
        )
        try:
            result = self.run_auto_ml(dataset)
        except InsufficientDataError as exc:
            await self._publish(
                ModelTrainingFailed(source="ml", model_type="automl", error=str(exc))
            )
            return {"status": "error", "detail": str(exc)}
        best = result.best_result
        if best is None:
            await self._publish(
                ModelTrainingFailed(source="ml", model_type="automl", error="sin candidatos")
            )
            return {"status": "error", "detail": "sin candidatos válidos"}
        approved, reasons = self.evaluate_model(best)
        record = self.register_model(
            best,
            author=author,
            result_note="; ".join(reasons),
        )
        metrics = to_metric_dict(best.metrics())
        await self._publish(
            ModelTrainingFinished(
                source="ml", model_id=record.id, model_type=record.model_type, metrics=metrics
            )
        )
        if approved:
            self._registry.approve(record.id, reasons="; ".join(reasons))
            await self._publish(
                ModelApproved(
                    source="ml",
                    model_id=record.id,
                    model_type=record.model_type,
                    metrics=metrics,
                    reasons=tuple(reasons),
                )
            )
            if self._ml.auto_activate:
                previous = self._registry.active_record()
                self.activate_model(record.id)
                await self._publish(
                    ModelActivated(
                        source="ml",
                        model_id=record.id,
                        model_type=record.model_type,
                        previous_id=previous.id if previous else None,
                    )
                )
        else:
            self._registry.reject(record.id, reasons="; ".join(reasons))
            await self._publish(
                ModelRejected(
                    source="ml",
                    model_id=record.id,
                    model_type=record.model_type,
                    reasons=tuple(reasons),
                )
            )
        self._experiments.save("automl", record.model_type, {"result": result.to_dict()})
        return {"status": "ok", "approved": approved, "model": record.to_dict(), "reasons": reasons}

    async def run_drift_check(self) -> dict[str, Any]:
        """Scheduled drift check; alerts and reduces confidence when needed."""
        try:
            report = self.detect_drift()
        except MLError as exc:
            return {"status": "error", "detail": str(exc)}
        if report.has_drift:
            worst = report.signals[0]
            await self._publish(
                DriftDetected(
                    source="ml",
                    kind=worst.kind,
                    metric=worst.metric,
                    value=worst.value,
                    threshold=worst.threshold,
                    action=worst.action,
                    detail=worst.detail,
                )
            )
            self._experiments.save("drift", worst.kind, report.to_dict())
        return {"status": "ok", **report.to_dict()}

    async def run_meta_evaluation(self) -> dict[str, Any]:
        """Scheduled meta-strategy governance cycle; publishes weight changes."""
        report = self.evaluate_meta()
        if report.weights:
            await self._publish(
                StrategyWeightsUpdated(
                    source="ml", weights=report.weights, reason="evaluación periódica"
                )
            )
        # Las decisiones de activación viajan una a una: el aplicador necesita
        # saber *qué* estrategia se desactiva y por qué, no sólo el mapa de
        # pesos. Sin esto el `disable` del MSM no llegaba a ninguna parte.
        for decision in report.decisions:
            if decision.get("action") not in ("disable", "enable"):
                continue
            await self._publish(
                MetaStrategyDecision(
                    source="ml",
                    strategy=str(decision["strategy"]),
                    action=str(decision["action"]),
                    detail=str(decision.get("detail", "")),
                    metrics={
                        k: float(v)
                        for k, v in dict(decision.get("metrics", {})).items()
                        if isinstance(v, int | float)
                    },
                )
            )
        self._experiments.save("meta", "governance", report.to_dict())
        return report.to_dict()

    # ------------------------------------------------------------------
    # Diagnóstico (dashboard)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Compact ML status for the dashboard."""
        return {
            "enabled": self._ml.enabled,
            "registry": self._registry.status(),
            "inference": self._inference.status(),
            "drift": self._last_drift.to_dict() if self._last_drift else {"has_drift": False},
            "meta": self._meta.status(),
            "monitor": self._monitor.metrics(),
            "features": self._feature_store.stats,
            "experiments": self._experiments.count(),
            "auto_activate": self._ml.auto_activate,
            # Qué historial entra al entrenamiento y qué queda fuera: sin esto,
            # auditar el ML obliga a releer el journal a mano.
            "data_quality": self.data_quality_report()["breakdown"],
        }

    def report(self) -> dict[str, Any]:
        """Full ML report (model cards + ranking + drift)."""
        active = self._registry.active_record()
        return {
            "active_model": self._reporter.model_card(active) if active else None,
            "models": [self._reporter.model_card(r) for r in self._registry.records()],
            "ranking": self._reporter.ranking_report(self.rank_strategies()),
            "drift": self._last_drift.to_dict() if self._last_drift else {"has_drift": False},
            "meta": self._meta.status(),
        }

    def feature_catalog(self) -> list[dict[str, Any]]:
        """The professional feature-store catalogue (for the dashboard)."""
        return self._feature_store.catalog()

    async def _publish(self, event: Event) -> None:
        """Publish an event tolerating a stopped/absent bus."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event)
        except Exception:  # el bus nunca debe tumbar el ML
            self._log.debug("No se pudo publicar %s", event.name)

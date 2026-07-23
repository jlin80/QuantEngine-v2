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
from app.ml.datasets import Dataset, DatasetBuilder
from app.ml.drift import DriftDetector, DriftReport
from app.ml.events import (
    DriftDetected,
    ModelActivated,
    ModelApproved,
    ModelRejected,
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
from app.ml.optimization import ParameterRecommender
from app.ml.prediction import Prediction
from app.ml.registry import ModelRecord, ModelRegistry
from app.ml.reporting import MLReporter
from app.ml.services import AIAdvisor, RiskAdvisor, StrategyIntelligence, StrategyScore
from app.ml.training import Trainer, TrainingResult

TradesProvider = Callable[[], Sequence[TradeRecord]]
"""Devuelve el historial de operaciones (normalmente el Trade Journal)."""


class MLEngine:
    """Facade over the Machine Learning layer.

    Args:
        settings: Configuración raíz (usa ``ml`` y ``execution``).
        bus: Event Bus para publicar hitos (opcional).
        trades_provider: Fuente del historial de operaciones (journal).
        persist: Si el registro y los experimentos escriben a disco.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        bus: EventBus | None = None,
        trades_provider: TradesProvider | None = None,
        persist: bool = True,
    ) -> None:
        self._settings = settings
        self._ml = settings.ml
        self._bus = bus
        self._trades_provider: TradesProvider = trades_provider or (lambda: [])
        self._log = logging.getLogger("app.ml")

        self._engineer = FeatureEngineer()
        self._feature_store = FeatureStore()
        self._feature_store.register_engineer(self._engineer)
        self._builder = DatasetBuilder(self._engineer)
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
        """Build a training dataset from the journalled trades."""
        return self._builder.build(self._trades_provider(), label=label)

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
        auc = max(result.holdout.auc, result.cross_validation.mean_auc)
        reasons: list[str] = []
        approved = True
        if result.dataset_summary.get("samples", 0) < v.min_samples:
            approved = False
            reasons.append(f"dataset insuficiente (<{v.min_samples})")
        if auc < v.min_auc:
            approved = False
            reasons.append(f"AUC {auc:.3f} < mínimo {v.min_auc}")
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
            reasons.append(f"supera los mínimos (AUC {auc:.3f})")
        return approved, reasons

    def register_model(
        self, result: TrainingResult, *, author: str = "system", result_note: str = ""
    ) -> ModelRecord:
        """Register a trained model version in the registry (never overwrites)."""
        return self._registry.register(
            result.model,
            metrics=to_metric_dict(result.metrics()),
            dataset=result.dataset_summary,
            feature_names=result.feature_names,
            author=author,
            result=result_note,
        )

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

    def evaluate_meta(self, labeled: Sequence[tuple[str, TradeRecord]] | None = None) -> MetaReport:
        """Run a meta-strategy governance cycle (adjusts weights/activation)."""
        return self._meta.evaluate(labeled if labeled is not None else self.labeled_trades())

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

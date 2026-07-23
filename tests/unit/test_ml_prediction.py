"""Inferencia explicable y detección de deriva (Fase 7)."""

from app.config.settings import MLDriftSettings, MLModelSettings, MLTrainingSettings
from app.ml.datasets import Dataset, DatasetBuilder
from app.ml.drift import DriftDetector, population_stability_index
from app.ml.features import FeatureEngineer
from app.ml.inference import InferenceService
from app.ml.models.factory import build_model
from app.ml.registry import ModelRegistry
from app.ml.training import Trainer

from tests.unit.ml_helpers import context, learnable_trades


def _active_registry() -> ModelRegistry:
    """A registry with a trained, active model over the learnable data."""
    dataset = DatasetBuilder().build(learnable_trades(80))
    result = Trainer(MLTrainingSettings()).train(
        lambda: build_model("logistic_regression", MLModelSettings(), seed=7), dataset
    )
    registry = ModelRegistry(None)
    record = registry.register(
        result.model,
        metrics=result.metrics(),
        dataset=result.dataset_summary,
        feature_names=result.feature_names,
    )
    registry.activate(record.id)
    return registry


# ---------------------------------------------------------------------------
# Inferencia + explicabilidad
# ---------------------------------------------------------------------------


def test_prediction_is_always_explained_never_just_a_number():
    service = InferenceService(_active_registry(), FeatureEngineer())
    prediction = service.predict(context())
    assert prediction.label in {"good", "bad"}
    assert 0.0 <= prediction.probability <= 1.0
    # Nunca sólo un número: explicación por variable + razones legibles.
    assert prediction.explanation
    assert prediction.reasons
    assert all("contribution" in item for item in prediction.explanation)
    assert prediction.model_id


def test_high_quality_context_scores_above_low_quality():
    service = InferenceService(_active_registry(), FeatureEngineer())
    good = service.predict(context(score=90.0, confidence=0.9, regime="trending"))
    bad = service.predict(
        context(score=15.0, confidence=0.2, regime="ranging", stop_loss=97.0, take_profit=101.0)
    )
    assert good.probability > bad.probability


def test_inference_degrades_gracefully_without_active_model():
    service = InferenceService(ModelRegistry(None), FeatureEngineer())
    prediction = service.predict(context())
    assert prediction.label == "unknown"
    assert prediction.probability == 0.5
    assert prediction.reasons  # explica que opera sólo con reglas
    assert not service.has_active_model


def test_confidence_factor_scales_prediction_confidence():
    service = InferenceService(_active_registry(), FeatureEngineer())
    full = service.predict(context()).confidence
    service.set_confidence_factor(0.5)
    reduced = service.predict(context()).confidence
    assert reduced < full or full == 0.0


# ---------------------------------------------------------------------------
# Deriva
# ---------------------------------------------------------------------------


def test_psi_is_zero_for_identical_and_large_for_shifted():
    reference = [float(i % 10) for i in range(200)]
    same = population_stability_index(reference, reference)
    shifted = population_stability_index(reference, [v + 20.0 for v in reference])
    assert same < 0.10
    assert shifted > 0.25


def _dataset_from(values: list[float], balance: float) -> Dataset:
    n = len(values)
    y = [1 if i < int(n * balance) else 0 for i in range(n)]
    return Dataset(feature_names=["f"], x=[[v] for v in values], y=y)


def test_feature_drift_is_flagged():
    detector = DriftDetector(MLDriftSettings())
    reference = _dataset_from([float(i % 10) for i in range(120)], 0.5)
    current = _dataset_from([float(i % 10) + 20.0 for i in range(120)], 0.5)
    signals = detector.feature_drift(reference, current)
    assert signals
    assert signals[0].kind == "feature"
    assert signals[0].action in {"reduce_confidence", "schedule_retrain"}


def test_concept_drift_detects_win_rate_shift():
    detector = DriftDetector(MLDriftSettings())
    reference = _dataset_from([float(i % 10) for i in range(120)], 0.5)
    current = _dataset_from([float(i % 10) for i in range(120)], 0.9)
    signal = detector.concept_drift(reference, current)
    assert signal is not None
    assert signal.kind == "concept"


def test_performance_drift_detects_auc_drop():
    detector = DriftDetector(MLDriftSettings())
    signal = detector.performance_drift(0.80, 0.60, kind="model")
    assert signal is not None
    assert signal.action == "schedule_retrain"
    # Sin caída relevante no hay señal.
    assert detector.performance_drift(0.80, 0.79) is None


def test_report_confidence_factor_reduces_only_on_drift():
    detector = DriftDetector(MLDriftSettings())
    reference = _dataset_from([float(i % 10) for i in range(120)], 0.5)
    drifted = detector.analyze(
        reference,
        _dataset_from([float(i % 10) + 20.0 for i in range(120)], 0.5),
    )
    clean = detector.analyze(reference, reference)
    assert drifted.has_drift
    assert drifted.confidence_factor(0.5) == 0.5
    assert not clean.has_drift
    assert clean.confidence_factor(0.5) == 1.0

"""Validación de modelos antes de activarlos (Fase 7).

Antes de aprobar un modelo se le exige evidencia: validación cruzada temporal
(walk-forward), evaluación en un holdout y comparación contra el modelo anterior.
Nunca se activa un modelo inferior al que ya está en producción.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ml.datasets.dataset import Dataset
from app.ml.evaluation.metrics import ClassificationMetrics, evaluate
from app.ml.interfaces.model import Model

ModelFactory = Callable[[], Model]
"""Construye un modelo sin entrenar (para re-entrenar por pliegue)."""


@dataclass(frozen=True, slots=True)
class CrossValidationResult:
    """Aggregate walk-forward cross-validation result."""

    folds: int
    mean_auc: float
    mean_accuracy: float
    mean_f1: float
    per_fold: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "folds": self.folds,
            "mean_auc": round(self.mean_auc, 4),
            "mean_accuracy": round(self.mean_accuracy, 4),
            "mean_f1": round(self.mean_f1, 4),
            "per_fold": self.per_fold,
        }


def cross_validate(
    factory: ModelFactory, dataset: Dataset, *, folds: int = 4
) -> CrossValidationResult:
    """Walk-forward cross-validation (expanding window, temporal order).

    Para cada pliegue ``i`` (a partir del segundo) se entrena con todos los
    pliegues anteriores y se valida sobre el pliegue ``i`` — nunca se mira el
    futuro. Devuelve las métricas promedio out-of-sample.
    """
    fold_indices = dataset.folds(folds)
    results: list[ClassificationMetrics] = []
    per_fold: list[dict[str, Any]] = []
    train_idx: list[int] = list(fold_indices[0]) if fold_indices else []
    for i in range(1, len(fold_indices)):
        test_idx = fold_indices[i]
        if _both_classes(dataset, train_idx) and test_idx:
            model = factory()
            model.fit([dataset.x[j] for j in train_idx], [dataset.y[j] for j in train_idx])
            proba = model.predict_proba([dataset.x[j] for j in test_idx])
            metrics = evaluate([dataset.y[j] for j in test_idx], proba)
            results.append(metrics)
            per_fold.append({"fold": i, **metrics.to_dict()})
        train_idx = train_idx + list(test_idx)
    if not results:
        return CrossValidationResult(0, 0.5, 0.0, 0.0, [])
    return CrossValidationResult(
        folds=len(results),
        mean_auc=_mean(m.auc for m in results),
        mean_accuracy=_mean(m.accuracy for m in results),
        mean_f1=_mean(m.f1 for m in results),
        per_fold=per_fold,
    )


def compare_to_previous(
    candidate: ClassificationMetrics,
    previous: ClassificationMetrics | None,
    *,
    min_improvement: float = 0.0,
) -> tuple[bool, list[str]]:
    """Decide whether a candidate beats the current model.

    Args:
        candidate: Métricas del modelo nuevo.
        previous: Métricas del modelo activo (``None`` si no hay).
        min_improvement: Mejora mínima de AUC exigida.

    Returns:
        ``(mejora, motivos)``. Sin modelo previo, siempre mejora.
    """
    if previous is None:
        return True, ["no hay modelo previo; se acepta como línea base"]
    delta = candidate.auc - previous.auc
    if delta >= min_improvement:
        return True, [f"AUC {candidate.auc:.3f} ≥ previo {previous.auc:.3f} (+{delta:.3f})"]
    return False, [f"AUC {candidate.auc:.3f} no mejora al previo {previous.auc:.3f} ({delta:+.3f})"]


def _both_classes(dataset: Dataset, indices: Sequence[int]) -> bool:
    """Whether a subset contains both classes (needed to train)."""
    labels = {dataset.y[i] for i in indices}
    return len(labels) >= 2


def _mean(values: Any) -> float:
    """Mean of an iterable (0.0 when empty)."""
    items = list(values)
    return sum(items) / len(items) if items else 0.0

"""Entrenamiento de modelos con evaluación temporal (Fase 7).

El flujo de un entrenamiento honesto: dividir temporalmente (holdout final),
entrenar y medir out-of-sample, validar por walk-forward y, sólo entonces,
reentrenar el modelo definitivo sobre **todo** el histórico para desplegarlo. El
``Trainer`` nunca activa nada: sólo produce evidencia (``TrainingResult``).
"""

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import MLTrainingSettings
from app.core.exceptions import InsufficientDataError
from app.ml.datasets.dataset import Dataset
from app.ml.evaluation.metrics import ClassificationMetrics, evaluate
from app.ml.evaluation.validation import (
    CrossValidationResult,
    ModelFactory,
    cross_validate,
)
from app.ml.interfaces.model import Model


@dataclass(slots=True)
class TrainingResult:
    """Outcome of a training run (model + out-of-sample evidence).

    Attributes:
        model: Modelo final entrenado sobre todo el dataset (para desplegar).
        model_type: Tipo del modelo.
        params: Hiperparámetros del modelo.
        holdout: Métricas sobre el holdout temporal final.
        cross_validation: Validación walk-forward sobre el dataset completo.
        n_train: Muestras de entrenamiento del holdout.
        n_test: Muestras del holdout.
        feature_names: Nombres de las features.
        dataset_summary: Resumen del dataset de origen.
    """

    model: Model
    model_type: str
    params: dict[str, Any]
    holdout: ClassificationMetrics
    cross_validation: CrossValidationResult
    n_train: int
    n_test: int
    feature_names: list[str]
    dataset_summary: dict[str, Any] = field(default_factory=dict)

    def metrics(self) -> dict[str, float]:
        """Headline metrics used by the validation gate (holdout + CV)."""
        return {
            "auc": self.holdout.auc,
            "accuracy": self.holdout.accuracy,
            "f1": self.holdout.f1,
            "cv_auc": self.cross_validation.mean_auc,
            "cv_accuracy": self.cross_validation.mean_accuracy,
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (never includes the raw model object)."""
        return {
            "model_type": self.model_type,
            "params": self.params,
            "holdout": self.holdout.to_dict(),
            "cross_validation": self.cross_validation.to_dict(),
            "n_train": self.n_train,
            "n_test": self.n_test,
            "feature_names": self.feature_names,
            "dataset": self.dataset_summary,
        }


class Trainer:
    """Train models with a temporal holdout and walk-forward validation.

    Args:
        settings: Configuración de entrenamiento (mínimos, holdout, pliegues).
    """

    def __init__(self, settings: MLTrainingSettings) -> None:
        self._settings = settings

    def train(
        self, factory: ModelFactory, dataset: Dataset, *, run_cross_validation: bool = True
    ) -> TrainingResult:
        """Train and validate a model built by ``factory`` on ``dataset``.

        Raises:
            InsufficientDataError: Si faltan muestras o falta una clase.
        """
        if len(dataset) < self._settings.min_samples:
            raise InsufficientDataError(
                f"Se requieren ≥{self._settings.min_samples} operaciones para entrenar; "
                f"hay {len(dataset)}.",
                context={"samples": len(dataset)},
            )
        train_ds, test_ds = dataset.split(self._settings.test_size)
        if len(set(train_ds.y)) < 2:
            raise InsufficientDataError(
                "El tramo de entrenamiento no contiene ambas clases (ganadoras y "
                "perdedoras); no se puede entrenar todavía.",
                context={"positives": train_ds.positives, "samples": len(train_ds)},
            )
        eval_model = factory()
        eval_model.fit(train_ds.x, train_ds.y)
        holdout = (
            evaluate(test_ds.y, eval_model.predict_proba(test_ds.x))
            if len(test_ds)
            else evaluate(train_ds.y, eval_model.predict_proba(train_ds.x))
        )

        cv = (
            cross_validate(factory, dataset, folds=self._settings.walk_forward_folds)
            if run_cross_validation
            else CrossValidationResult(0, 0.5, 0.0, 0.0, [])
        )

        final_model = factory()
        final_model.fit(dataset.x, dataset.y)
        return TrainingResult(
            model=final_model,
            model_type=final_model.model_type.value,
            params=final_model.params(),
            holdout=holdout,
            cross_validation=cv,
            n_train=len(train_ds),
            n_test=len(test_ds),
            feature_names=dataset.feature_names,
            dataset_summary=dataset.to_dict(),
        )

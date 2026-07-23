"""Ensemble Engine: combina varios modelos tras la misma interfaz (Fase 7).

Voting (soft/hard/weighted/dynamic) y stacking. El ensemble es a su vez un
``Model``, así que el resto del sistema lo trata como a cualquier otro. La
votación dinámica permite que el Meta Strategy Manager ajuste los pesos de cada
modelo en caliente según su rendimiento reciente.
"""

from collections.abc import Sequence
from typing import Any

from app.core.exceptions import MLError, ModelNotFittedError
from app.ml.interfaces.model import Model, ModelType, as_labels, as_matrix
from app.ml.models.logistic_regression import LogisticRegressionModel
from app.ml.models.math import normalize


class VotingEnsemble(Model):
    """Combine base models by soft/hard/weighted/dynamic voting.

    Args:
        models: Modelos base (ya construidos, sin entrenar).
        mode: ``soft`` (media de probabilidades), ``hard`` (voto mayoritario),
            ``weighted``/``dynamic`` (media ponderada; los pesos se pueden ajustar
            en caliente con :meth:`set_weights`).
        weights: Pesos iniciales (uno por modelo); por defecto uniformes.
    """

    def __init__(
        self,
        models: Sequence[Model],
        *,
        mode: str = "soft",
        weights: Sequence[float] | None = None,
    ) -> None:
        super().__init__()
        if not models:
            raise MLError("Un ensemble necesita al menos un modelo base")
        if mode not in {"soft", "hard", "weighted", "dynamic"}:
            raise MLError(f"Modo de votación desconocido: '{mode}'")
        self._models = list(models)
        self._mode = mode
        self._weights = list(weights) if weights else [1.0] * len(self._models)
        self._fitted = False

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.VOTING

    @property
    def is_fitted(self) -> bool:
        """Whether every base model has been trained."""
        return self._fitted

    @property
    def mode(self) -> str:
        """Active voting mode."""
        return self._mode

    def set_weights(self, weights: Sequence[float]) -> None:
        """Update per-model weights (dynamic voting)."""
        if len(weights) != len(self._models):
            raise MLError("El número de pesos no coincide con el de modelos")
        self._weights = list(weights)

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train every base model on the same data."""
        matrix = as_matrix(x)
        labels = as_labels(y)
        self._remember_shape(matrix)
        for model in self._models:
            model.fit(matrix, labels)
        self._fitted = True

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Combine base probabilities according to the voting mode."""
        self._ensure_fitted()
        rows = as_matrix(x)
        per_model = [model.predict_proba(rows) for model in self._models]
        if self._mode == "hard":
            votes = [[1.0 if p >= 0.5 else 0.0 for p in preds] for preds in per_model]
            return [sum(col) / len(votes) for col in zip(*votes, strict=False)]
        weights = normalize(self._weights) if self._mode in {"weighted", "dynamic"} else None
        result: list[float] = []
        for i in range(len(rows)):
            column = [preds[i] for preds in per_model]
            if weights is None:
                result.append(sum(column) / len(column))
            else:
                result.append(sum(w * p for w, p in zip(weights, column, strict=False)))
        return result

    def feature_importances(self) -> list[float]:
        """Weighted mean of base-model importances."""
        self._ensure_fitted()
        weights = normalize(self._weights)
        totals = [0.0] * self._n_features
        for weight, model in zip(weights, self._models, strict=False):
            for j, imp in enumerate(model.feature_importances()):
                if j < len(totals):
                    totals[j] += weight * imp
        return normalize(totals)

    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the ensemble."""
        return {
            "mode": self._mode,
            "weights": self._weights,
            "members": [m.model_type.value for m in self._models],
        }

    def _ensure_fitted(self) -> None:
        """Raise if a prediction is requested before training."""
        if not self._fitted:
            raise ModelNotFittedError("VotingEnsemble no ha sido entrenado")


class StackingEnsemble(Model):
    """Stacking: a meta-model learns to combine base-model probabilities.

    Args:
        models: Modelos base.
        meta: Meta-modelo (por defecto regresión logística, explicable).
        holdout: Fracción final usada para entrenar el meta-modelo.
    """

    def __init__(
        self,
        models: Sequence[Model],
        *,
        meta: Model | None = None,
        holdout: float = 0.4,
    ) -> None:
        super().__init__()
        if not models:
            raise MLError("Un stacking necesita al menos un modelo base")
        self._models = list(models)
        self._meta = meta or LogisticRegressionModel()
        self._holdout = holdout
        self._fitted = False

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.STACKING

    @property
    def is_fitted(self) -> bool:
        """Whether the stack has been trained."""
        return self._fitted

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train bases on the first block, the meta on the held-out block."""
        matrix = as_matrix(x)
        labels = as_labels(y)
        self._remember_shape(matrix)
        n = len(matrix)
        cut = max(1, round(n * (1.0 - self._holdout))) if n else 0
        cut = min(cut, n - 1) if n > 1 else n
        train_x, train_y = matrix[:cut], labels[:cut]
        meta_x, meta_y = matrix[cut:], labels[cut:]
        for model in self._models:
            model.fit(train_x, train_y)
        if meta_x and len(set(meta_y)) >= 2:
            meta_features = [[m.predict_one(row) for m in self._models] for row in meta_x]
            self._meta.fit(meta_features, meta_y)
        # Reentrenar las bases sobre todo el dataset para desplegar.
        for model in self._models:
            model.fit(matrix, labels)
        self._fitted = True

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Feed base predictions into the meta-model."""
        self._ensure_fitted()
        rows = as_matrix(x)
        meta_features = [[m.predict_one(row) for m in self._models] for row in rows]
        if not self._meta.is_fitted:
            return [sum(f) / len(f) for f in meta_features]
        return self._meta.predict_proba(meta_features)

    def feature_importances(self) -> list[float]:
        """Combine base importances weighted by the meta-model's weights."""
        self._ensure_fitted()
        try:
            meta_imp = self._meta.feature_importances()
        except ModelNotFittedError:
            meta_imp = [1.0 / len(self._models)] * len(self._models)
        totals = [0.0] * self._n_features
        for weight, model in zip(meta_imp, self._models, strict=False):
            for j, imp in enumerate(model.feature_importances()):
                if j < len(totals):
                    totals[j] += weight * imp
        return normalize(totals)

    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the stack."""
        return {
            "meta": self._meta.model_type.value,
            "holdout": self._holdout,
            "members": [m.model_type.value for m in self._models],
        }

    def _ensure_fitted(self) -> None:
        """Raise if a prediction is requested before training."""
        if not self._fitted:
            raise ModelNotFittedError("StackingEnsemble no ha sido entrenado")


def build_ensemble(
    mode: str,
    models: Sequence[Model],
    *,
    weights: Sequence[float] | None = None,
    meta: Model | None = None,
) -> Model:
    """Build a voting or stacking ensemble from base models.

    Args:
        mode: ``soft`` | ``hard`` | ``weighted`` | ``dynamic`` | ``stacking``.
        models: Modelos base.
        weights: Pesos (sólo voting ponderado/dinámico).
        meta: Meta-modelo (sólo stacking).

    Returns:
        Un :class:`Model` que combina los modelos base.
    """
    if mode == "stacking":
        return StackingEnsemble(models, meta=meta)
    return VotingEnsemble(models, mode=mode, weights=weights)

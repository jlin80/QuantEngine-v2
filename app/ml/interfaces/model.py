"""Contrato común de todos los modelos de Machine Learning.

Todos los modelos son **intercambiables** tras esta interfaz: clasificación
binaria de la calidad de una operación (1 = ganadora, 0 = perdedora). Ningún
modelo predice el precio. Toda predicción es explicable: además de la
probabilidad, cada modelo expone la importancia de las variables y la
contribución por variable de una fila concreta (nunca una caja negra).
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

Vector = list[float]
"""Fila de features (valores ya numéricos)."""

Matrix = list[list[float]]
"""Matriz de features fila-por-fila."""


class ModelType(StrEnum):
    """Tipos de modelo soportados por la infraestructura.

    Los cuatro primeros están implementados en Python puro (sin dependencias);
    los backends pesados (XGBoost/LightGBM/CatBoost/redes) quedan preparados tras
    la misma interfaz y fallan con un mensaje claro si su librería no está.
    """

    LOGISTIC_REGRESSION = "logistic_regression"
    DECISION_TREE = "decision_tree"
    RANDOM_FOREST = "random_forest"
    EXTRA_TREES = "extra_trees"
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"
    CATBOOST = "catboost"
    NEURAL_NET = "neural_net"
    VOTING = "voting"
    STACKING = "stacking"

    @property
    def is_native(self) -> bool:
        """Whether the type has a pure-Python (dependency-free) implementation."""
        return self in _NATIVE_TYPES


_NATIVE_TYPES = frozenset(
    {
        ModelType.LOGISTIC_REGRESSION,
        ModelType.DECISION_TREE,
        ModelType.RANDOM_FOREST,
        ModelType.EXTRA_TREES,
    }
)


def as_matrix(rows: Sequence[Sequence[float]]) -> Matrix:
    """Normalise any row sequence to a concrete ``list[list[float]]``."""
    return [[float(value) for value in row] for row in rows]


def as_labels(labels: Sequence[int]) -> list[int]:
    """Normalise a label sequence to ``list[int]`` (0/1)."""
    return [int(label) for label in labels]


class Model(ABC):
    """Base class for every interchangeable classifier.

    Subclases implementan ``fit``/``predict_proba``/``feature_importances`` y
    ``params``; los métodos de conveniencia (``predict``, ``predict_one``,
    ``contributions``) se derivan aquí de forma uniforme.
    """

    @property
    @abstractmethod
    def model_type(self) -> ModelType:
        """Concrete model type."""

    @property
    @abstractmethod
    def is_fitted(self) -> bool:
        """Whether the model has been trained."""

    @property
    def n_features(self) -> int:
        """Number of input features seen at fit time (0 if not fitted)."""
        return self._n_features

    def __init__(self) -> None:
        self._n_features = 0

    @abstractmethod
    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train the model on features ``x`` and binary labels ``y``."""

    @abstractmethod
    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Return P(class == 1) for each row."""

    @abstractmethod
    def feature_importances(self) -> list[float]:
        """Non-negative importance per feature (sums to 1 when meaningful)."""

    @abstractmethod
    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the model (JSON-safe)."""

    def predict(self, x: Sequence[Sequence[float]], *, threshold: float = 0.5) -> list[int]:
        """Return a 0/1 prediction per row using ``threshold`` on the probability."""
        return [1 if p >= threshold else 0 for p in self.predict_proba(x)]

    def predict_one(self, row: Sequence[float]) -> float:
        """Return P(class == 1) for a single row."""
        return self.predict_proba([row])[0]

    def contributions(self, row: Sequence[float]) -> list[float]:
        """Per-feature signed contribution for a single row (explainability).

        Por defecto se aproxima con ``importancia × (valor - 0)``; los modelos
        lineales la sobreescriben con la contribución exacta (coeficiente × valor
        estandarizado). Nunca se devuelve sólo un número: esto alimenta la
        explicación legible del ``InferenceService``.
        """
        importances = self.feature_importances()
        return [imp * float(value) for imp, value in zip(importances, row, strict=False)]

    def _remember_shape(self, x: Sequence[Sequence[float]]) -> None:
        """Store the feature count from a training matrix."""
        self._n_features = len(x[0]) if x else 0

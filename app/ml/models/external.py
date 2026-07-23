"""Backends de modelos pesados — infraestructura preparada (Fase 7).

XGBoost, LightGBM, CatBoost y redes neuronales quedan disponibles tras la misma
interfaz ``Model``, pero **no** se instalan por defecto: son dependencias
pesadas y algunas no arrancan en CPUs sin SSE4.2. Igual que los optimizadores
bayesiano/Optuna del laboratorio (Fase 6), estos adaptadores cargan su librería
de forma perezosa y, si no está instalada, fallan con un mensaje claro indicando
el paquete pip necesario. Si la librería está presente, funcionan sin cambios.
"""

import importlib
from collections.abc import Sequence
from typing import Any

from app.core.exceptions import ModelBackendUnavailableError, ModelNotFittedError
from app.ml.interfaces.model import Model, ModelType, as_labels, as_matrix


def _load(module_name: str, pip_name: str) -> Any:
    """Import an optional backend module or raise a clear, actionable error."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:  # dependencia pesada no instalada (lo normal aquí)
        raise ModelBackendUnavailableError(
            f"El backend '{module_name}' no está instalado. La infraestructura está "
            f"preparada; instala `pip install {pip_name}` para habilitarlo.",
            context={"module": module_name, "pip": pip_name},
        ) from exc


class _ExternalModel(Model):
    """Adapter over a scikit-learn-style estimator loaded lazily."""

    _module_name: str = ""
    _pip_name: str = ""

    def __init__(self, **hyperparameters: Any) -> None:
        super().__init__()
        self._hyperparameters = hyperparameters
        self._estimator: Any = None
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        """Whether the estimator has been trained."""
        return self._fitted

    def _build_estimator(self) -> Any:
        """Instantiate the concrete estimator (subclasses override)."""
        raise NotImplementedError

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train the backend estimator (raises if the backend is missing)."""
        matrix = as_matrix(x)
        labels = as_labels(y)
        self._remember_shape(matrix)
        self._estimator = self._build_estimator()
        self._estimator.fit(matrix, labels)
        self._fitted = True

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Return P(class == 1) for each row."""
        if not self._fitted or self._estimator is None:
            raise ModelNotFittedError(f"{type(self).__name__} no ha sido entrenado")
        proba = self._estimator.predict_proba(as_matrix(x))
        return [float(row[1]) for row in proba]

    def feature_importances(self) -> list[float]:
        """Backend feature importances, or a uniform fallback."""
        if self._estimator is not None and hasattr(self._estimator, "feature_importances_"):
            values = [float(v) for v in self._estimator.feature_importances_]
            total = sum(values)
            if total > 0:
                return [v / total for v in values]
        n = self._n_features
        return [1.0 / n] * n if n else []

    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the model."""
        return dict(self._hyperparameters)


class XGBoostModel(_ExternalModel):
    """XGBoost gradient boosting (prepared — requires ``xgboost``)."""

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.XGBOOST

    def _build_estimator(self) -> Any:
        """Build an ``xgboost.XGBClassifier`` (lazy import)."""
        xgboost = _load("xgboost", "xgboost")
        return xgboost.XGBClassifier(**self._hyperparameters)


class LightGBMModel(_ExternalModel):
    """LightGBM gradient boosting (prepared — requires ``lightgbm``)."""

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.LIGHTGBM

    def _build_estimator(self) -> Any:
        """Build a ``lightgbm.LGBMClassifier`` (lazy import)."""
        lightgbm = _load("lightgbm", "lightgbm")
        return lightgbm.LGBMClassifier(**self._hyperparameters)


class CatBoostModel(_ExternalModel):
    """CatBoost gradient boosting (prepared — requires ``catboost``)."""

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.CATBOOST

    def _build_estimator(self) -> Any:
        """Build a ``catboost.CatBoostClassifier`` (lazy import)."""
        catboost = _load("catboost", "catboost")
        return catboost.CatBoostClassifier(verbose=False, **self._hyperparameters)


class NeuralNetModel(_ExternalModel):
    """Feed-forward neural network (prepared — requires ``scikit-learn``)."""

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.NEURAL_NET

    def _build_estimator(self) -> Any:
        """Build a ``sklearn.neural_network.MLPClassifier`` (lazy import)."""
        module = _load("sklearn.neural_network", "scikit-learn")
        return module.MLPClassifier(**self._hyperparameters)

    def feature_importances(self) -> list[float]:
        """Neural nets expose no native importances: uniform fallback."""
        n = self._n_features
        return [1.0 / n] * n if n else []

"""Regresión logística (Python puro) — el modelo explicable por defecto.

Descenso de gradiente por lotes con regularización L2 sobre features
estandarizadas. Es el modelo por defecto precisamente por su transparencia: los
coeficientes (en escala estandarizada) son directamente comparables entre sí y
la contribución de cada variable a una predicción es ``coeficiente × valor``.
Nada de caja negra.
"""

from collections.abc import Sequence
from typing import Any

from app.core.exceptions import ModelNotFittedError
from app.ml.interfaces.model import Model, ModelType, as_labels, as_matrix
from app.ml.models.math import dot, sigmoid
from app.ml.models.preprocessing import StandardScaler


class LogisticRegressionModel(Model):
    """Binary logistic regression trained with batch gradient descent.

    Args:
        learning_rate: Tasa de aprendizaje del descenso de gradiente.
        epochs: Número de pasadas completas sobre el dataset.
        l2: Coeficiente de regularización L2 (0 lo desactiva).
    """

    def __init__(self, *, learning_rate: float = 0.1, epochs: int = 400, l2: float = 0.001) -> None:
        super().__init__()
        self._lr = learning_rate
        self._epochs = epochs
        self._l2 = l2
        self._weights: list[float] = []
        self._bias = 0.0
        self._scaler = StandardScaler()
        self._fitted = False

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.LOGISTIC_REGRESSION

    @property
    def is_fitted(self) -> bool:
        """Whether the model has been trained."""
        return self._fitted

    @property
    def coefficients(self) -> list[float]:
        """Learned weights on the standardised feature scale."""
        return list(self._weights)

    @property
    def intercept(self) -> float:
        """Learned bias term."""
        return self._bias

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train via full-batch gradient descent on standardised features."""
        matrix = as_matrix(x)
        labels = as_labels(y)
        self._remember_shape(matrix)
        n_features = self._n_features
        self._weights = [0.0] * n_features
        self._bias = 0.0
        if not matrix:
            self._fitted = True
            return
        scaled = self._scaler.fit_transform(matrix)
        n = len(scaled)
        for _ in range(self._epochs):
            grad_w = [0.0] * n_features
            grad_b = 0.0
            for row, label in zip(scaled, labels, strict=False):
                error = sigmoid(dot(self._weights, row) + self._bias) - label
                for j in range(n_features):
                    grad_w[j] += error * row[j]
                grad_b += error
            for j in range(n_features):
                gradient = grad_w[j] / n + self._l2 * self._weights[j]
                self._weights[j] -= self._lr * gradient
            self._bias -= self._lr * (grad_b / n)
        self._fitted = True

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Return P(class == 1) for each row."""
        self._ensure_fitted()
        scaled = self._scaler.transform(as_matrix(x))
        return [sigmoid(dot(self._weights, row) + self._bias) for row in scaled]

    def feature_importances(self) -> list[float]:
        """Absolute standardised coefficients, normalised to sum 1."""
        self._ensure_fitted()
        magnitudes = [abs(w) for w in self._weights]
        total = sum(magnitudes)
        if total <= 0.0:
            n = len(magnitudes)
            return [1.0 / n] * n if n else []
        return [m / total for m in magnitudes]

    def contributions(self, row: Sequence[float]) -> list[float]:
        """Exact signed contribution per feature: ``coef × standardised value``."""
        self._ensure_fitted()
        scaled = self._scaler.transform_row(row)
        return [w * z for w, z in zip(self._weights, scaled, strict=False)]

    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the model."""
        return {"learning_rate": self._lr, "epochs": self._epochs, "l2": self._l2}

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialisation of the trained model."""
        return {
            "type": self.model_type.value,
            "params": self.params(),
            "weights": self._weights,
            "bias": self._bias,
            "scaler": self._scaler.to_dict(),
            "n_features": self._n_features,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LogisticRegressionModel":
        """Rebuild a trained model from :meth:`to_dict` output."""
        params = data.get("params", {})
        model = cls(
            learning_rate=float(params.get("learning_rate", 0.1)),
            epochs=int(params.get("epochs", 400)),
            l2=float(params.get("l2", 0.001)),
        )
        model._weights = [float(w) for w in data.get("weights", [])]
        model._bias = float(data.get("bias", 0.0))
        model._scaler = StandardScaler.from_dict(data.get("scaler", {}))
        model._n_features = int(data.get("n_features", len(model._weights)))
        model._fitted = True
        return model

    def _ensure_fitted(self) -> None:
        """Raise if a prediction is requested before training."""
        if not self._fitted:
            raise ModelNotFittedError("LogisticRegressionModel no ha sido entrenado")

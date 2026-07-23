"""Preprocesamiento de features (Python puro).

El ``StandardScaler`` centra y escala cada columna (media 0, desviación 1). Es
determinista y serializable, para poder guardarlo junto al modelo en el registro
y reproducir exactamente la inferencia.
"""

from collections.abc import Sequence
from typing import Any

from app.ml.models.math import mean, pstdev


class StandardScaler:
    """Standardise features column-wise: ``(x - mean) / std``.

    Las columnas con desviación nula se dejan intactas (evita dividir por cero).
    """

    def __init__(self) -> None:
        self._means: list[float] = []
        self._stds: list[float] = []
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        """Whether :meth:`fit` has been called."""
        return self._fitted

    def fit(self, x: Sequence[Sequence[float]]) -> "StandardScaler":
        """Learn per-column mean and standard deviation."""
        if not x:
            self._means, self._stds, self._fitted = [], [], True
            return self
        n_features = len(x[0])
        self._means = []
        self._stds = []
        for j in range(n_features):
            column = [float(row[j]) for row in x]
            mu = mean(column)
            sigma = pstdev(column, known_mean=mu)
            self._means.append(mu)
            self._stds.append(sigma if sigma > 1e-12 else 1.0)
        self._fitted = True
        return self

    def transform(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        """Apply the learned standardisation to a matrix."""
        return [self.transform_row(row) for row in x]

    def transform_row(self, row: Sequence[float]) -> list[float]:
        """Apply the learned standardisation to a single row."""
        return [
            (float(value) - mu) / sigma
            for value, mu, sigma in zip(row, self._means, self._stds, strict=False)
        ]

    def fit_transform(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        """Fit and transform in one call."""
        return self.fit(x).transform(x)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialisation of the fitted statistics."""
        return {"means": self._means, "stds": self._stds, "fitted": self._fitted}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StandardScaler":
        """Rebuild a scaler from :meth:`to_dict` output."""
        scaler = cls()
        scaler._means = [float(v) for v in data.get("means", [])]
        scaler._stds = [float(v) for v in data.get("stds", [])]
        scaler._fitted = bool(data.get("fitted", False))
        return scaler

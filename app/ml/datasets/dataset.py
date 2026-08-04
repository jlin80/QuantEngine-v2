"""Dataset tipado para el ML (features + etiquetas + metadatos).

Inmutable en la práctica: se construye una vez y se consume. Los cortes de
validación son **temporales por defecto** (las primeras filas entrenan, las
últimas validan) porque las operaciones son una serie temporal — barajar
filtraría información del futuro.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Dataset:
    """A supervised dataset: feature matrix ``x`` and binary labels ``y``.

    Attributes:
        feature_names: Nombres de columna (mismo orden que cada fila de ``x``).
        x: Matriz de features fila-por-fila.
        y: Etiquetas binarias (0/1), una por fila.
        metadata: Procedencia y estadística del dataset (JSON-safe).
    """

    feature_names: list[str]
    x: list[list[float]]
    y: list[int]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        """Number of samples."""
        return len(self.x)

    @property
    def n_features(self) -> int:
        """Number of features per sample."""
        return len(self.feature_names)

    @property
    def positives(self) -> int:
        """Count of positive (class == 1) samples."""
        return sum(self.y)

    @property
    def balance(self) -> float:
        """Fraction of positive samples (0.0 for an empty dataset)."""
        return self.positives / len(self.y) if self.y else 0.0

    @property
    def sample_weights(self) -> list[float]:
        """Per-row training weight (``1.0`` cuando no se declaró ninguno).

        Los pone el ``DatasetBuilder`` según la era de ejecución de cada
        operación, para no aprender de bugs ya arreglados.
        """
        weights = self.metadata.get("sample_weights")
        if isinstance(weights, list) and len(weights) == len(self.x):
            return [float(w) for w in weights]
        return [1.0] * len(self.x)

    def subset(self, indices: Sequence[int]) -> "Dataset":
        """Build a dataset from a subset of row indices (preserves order)."""
        rows = list(indices)
        metadata = {**self.metadata, "subset_of": self.metadata.get("id")}
        # Los vectores por fila deben cortarse con las filas: si no, un split
        # deja los pesos desalineados y cada muestra hereda el peso de otra.
        for key in ("sample_weights", "sample_eras"):
            values = self.metadata.get(key)
            if isinstance(values, list) and len(values) == len(self.x):
                metadata[key] = [values[i] for i in rows]
        return Dataset(
            feature_names=self.feature_names,
            x=[self.x[i] for i in rows],
            y=[self.y[i] for i in rows],
            metadata=metadata,
        )

    def split(self, test_size: float = 0.25) -> tuple["Dataset", "Dataset"]:
        """Temporal train/test split (no shuffle).

        Args:
            test_size: Fracción final reservada a validación [0, 1).

        Returns:
            ``(train, test)`` conservando el orden temporal.
        """
        n = len(self.x)
        cut = max(1, round(n * (1.0 - test_size))) if n else 0
        cut = min(cut, n)
        return self.subset(range(cut)), self.subset(range(cut, n))

    def folds(self, k: int) -> list[list[int]]:
        """Contiguous index folds for cross-validation (temporal order kept)."""
        n = len(self.x)
        if k <= 1 or n < k:
            return [list(range(n))] if n else []
        size = n // k
        result: list[list[int]] = []
        start = 0
        for i in range(k):
            end = n if i == k - 1 else start + size
            result.append(list(range(start, end)))
            start = end
        return result

    def to_dict(self) -> dict[str, Any]:
        """Compact JSON-safe summary (not the full matrix)."""
        return {
            "samples": len(self),
            "n_features": self.n_features,
            "feature_names": self.feature_names,
            "positives": self.positives,
            "balance": round(self.balance, 4),
            "metadata": self.metadata,
        }

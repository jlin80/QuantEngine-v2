"""Árbol de decisión CART (Python puro) para clasificación binaria.

Sirve como modelo por sí mismo (``DecisionTreeModel``) y como aprendiz base de
los bosques (``RandomForest``/``ExtraTrees``). Soporta dos formas de elegir el
umbral de corte: ``best`` (busca el mejor punto medio por impureza de Gini) y
``random`` (umbral aleatorio, usado por Extra Trees). La importancia de cada
variable es la reducción de impureza acumulada — plenamente trazable.
"""

import itertools
import random
from collections.abc import Sequence
from typing import Any

from app.core.exceptions import ModelNotFittedError
from app.ml.interfaces.model import Model, ModelType, as_labels, as_matrix
from app.ml.models.math import gini_impurity, normalize


class _TreeNode:
    """A node in the decision tree (internal split or leaf)."""

    __slots__ = ("feature", "left", "right", "threshold", "value")

    def __init__(
        self,
        *,
        value: float,
        feature: int = -1,
        threshold: float = 0.0,
        left: "_TreeNode | None" = None,
        right: "_TreeNode | None" = None,
    ) -> None:
        self.value = value  # P(class == 1) at the node
        self.feature = feature
        self.threshold = threshold
        self.left = left
        self.right = right

    @property
    def is_leaf(self) -> bool:
        """Whether the node is a leaf."""
        return self.left is None or self.right is None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialisation of the subtree rooted here."""
        if self.is_leaf:
            return {"v": self.value}
        left = self.left
        right = self.right
        if left is None or right is None:  # pragma: no cover - is_leaf ya lo cubre
            return {"v": self.value}
        return {
            "v": self.value,
            "f": self.feature,
            "t": self.threshold,
            "l": left.to_dict(),
            "r": right.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_TreeNode":
        """Rebuild a subtree from :meth:`to_dict` output."""
        if "l" not in data or "r" not in data:
            return cls(value=float(data["v"]))
        return cls(
            value=float(data["v"]),
            feature=int(data["f"]),
            threshold=float(data["t"]),
            left=cls.from_dict(data["l"]),
            right=cls.from_dict(data["r"]),
        )


class DecisionTreeModel(Model):
    """CART decision tree for binary classification.

    Args:
        max_depth: Profundidad máxima del árbol.
        min_samples_split: Mínimo de muestras para intentar un corte.
        max_features: Fracción de variables candidatas por corte (1.0 = todas).
        splitter: ``best`` (mejor umbral por Gini) o ``random`` (Extra Trees).
        seed: Semilla para el submuestreo/umbral aleatorio (determinismo).
    """

    def __init__(
        self,
        *,
        max_depth: int = 6,
        min_samples_split: int = 6,
        max_features: float = 1.0,
        splitter: str = "best",
        seed: int = 7,
    ) -> None:
        super().__init__()
        self._max_depth = max_depth
        self._min_samples_split = min_samples_split
        self._max_features = max_features
        self._splitter = splitter
        self._seed = seed
        self._rng = random.Random(seed)
        self._root: _TreeNode | None = None
        self._importances: list[float] = []
        self._fitted = False
        # Poblados sólo durante el entrenamiento (no se retiene el dataset).
        self._x: list[list[float]] = []
        self._y: list[int] = []

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.DECISION_TREE

    @property
    def is_fitted(self) -> bool:
        """Whether the model has been trained."""
        return self._fitted

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Grow the tree greedily minimising Gini impurity."""
        matrix = as_matrix(x)
        labels = as_labels(y)
        self._remember_shape(matrix)
        self._rng = random.Random(self._seed)
        self._importances = [0.0] * self._n_features
        if not matrix:
            self._root = _TreeNode(value=0.0)
            self._fitted = True
            return
        self._x = matrix
        self._y = labels
        self._root = self._build(list(range(len(matrix))), depth=0)
        self._importances = normalize(self._importances)
        self._fitted = True

    def _build(self, indices: list[int], *, depth: int) -> _TreeNode:
        """Recursively build a subtree over the given sample indices."""
        total = len(indices)
        positives = sum(self._y[i] for i in indices)
        value = positives / total if total else 0.0
        impurity = gini_impurity(positives, total)
        if depth >= self._max_depth or total < self._min_samples_split or impurity <= 1e-12:
            return _TreeNode(value=value)

        split = self._best_split(indices, impurity)
        if split is None:
            return _TreeNode(value=value)
        feature, threshold, left_idx, right_idx, gain = split
        self._importances[feature] += total * gain
        return _TreeNode(
            value=value,
            feature=feature,
            threshold=threshold,
            left=self._build(left_idx, depth=depth + 1),
            right=self._build(right_idx, depth=depth + 1),
        )

    def _candidate_features(self) -> list[int]:
        """Pick the feature subset considered at a split."""
        n = self._n_features
        if self._max_features >= 1.0:
            return list(range(n))
        k = max(1, round(self._max_features * n))
        return self._rng.sample(range(n), k)

    def _best_split(
        self, indices: list[int], impurity: float
    ) -> tuple[int, float, list[int], list[int], float] | None:
        """Find the split that maximises Gini gain (or a random one)."""
        total = len(indices)
        best: tuple[int, float, list[int], list[int], float] | None = None
        best_score = 0.0
        for feature in self._candidate_features():
            values = [self._x[i][feature] for i in indices]
            for threshold in self._thresholds(values):
                left_idx = [i for i in indices if self._x[i][feature] <= threshold]
                right_idx = [i for i in indices if self._x[i][feature] > threshold]
                if not left_idx or not right_idx:
                    continue
                child = self._weighted_impurity(left_idx, right_idx, total)
                gain = impurity - child
                if gain > best_score + 1e-12:
                    best_score = gain
                    best = (feature, threshold, left_idx, right_idx, gain)
        return best

    def _thresholds(self, values: list[float]) -> list[float]:
        """Candidate thresholds for a feature (midpoints or one random)."""
        unique = sorted(set(values))
        if len(unique) < 2:
            return []
        if self._splitter == "random":
            low, high = unique[0], unique[-1]
            return [self._rng.uniform(low, high)]
        return [(a + b) / 2.0 for a, b in itertools.pairwise(unique)]

    def _weighted_impurity(self, left_idx: list[int], right_idx: list[int], total: int) -> float:
        """Weighted Gini impurity of a candidate split's children."""
        nl, nr = len(left_idx), len(right_idx)
        gl = gini_impurity(sum(self._y[i] for i in left_idx), nl)
        gr = gini_impurity(sum(self._y[i] for i in right_idx), nr)
        return (nl / total) * gl + (nr / total) * gr

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Return P(class == 1) for each row."""
        self._ensure_fitted()
        return [self._predict_row(row) for row in x]

    def _predict_row(self, row: Sequence[float]) -> float:
        """Traverse the tree to the leaf holding ``row``."""
        node = self._root
        if node is None:
            return 0.0
        while not node.is_leaf and node.left is not None and node.right is not None:
            node = node.left if row[node.feature] <= node.threshold else node.right
        return node.value

    def feature_importances(self) -> list[float]:
        """Normalised impurity-decrease importance per feature."""
        self._ensure_fitted()
        if self._importances:
            return list(self._importances)
        return [1.0 / self._n_features] * self._n_features if self._n_features else []

    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the model."""
        return {
            "max_depth": self._max_depth,
            "min_samples_split": self._min_samples_split,
            "max_features": self._max_features,
            "splitter": self._splitter,
            "seed": self._seed,
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialisation of the trained tree.

        No incluye el dataset (``_x``/``_y``): son andamiaje del entrenamiento y
        el árbol ya no los necesita para predecir.
        """
        return {
            "type": self.model_type.value,
            "params": self.params(),
            "root": self._root.to_dict() if self._root is not None else None,
            "importances": list(self._importances),
            "n_features": self._n_features,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionTreeModel":
        """Rebuild a trained tree from :meth:`to_dict` output."""
        params = data.get("params", {})
        model = cls(
            max_depth=int(params.get("max_depth", 6)),
            min_samples_split=int(params.get("min_samples_split", 6)),
            max_features=float(params.get("max_features", 1.0)),
            splitter=str(params.get("splitter", "best")),
            seed=int(params.get("seed", 7)),
        )
        root = data.get("root")
        model._root = None if root is None else _TreeNode.from_dict(root)
        model._importances = [float(v) for v in data.get("importances", [])]
        model._n_features = int(data.get("n_features", len(model._importances)))
        model._fitted = True
        return model

    def _ensure_fitted(self) -> None:
        """Raise if a prediction is requested before training."""
        if not self._fitted or self._root is None:
            raise ModelNotFittedError("DecisionTreeModel no ha sido entrenado")

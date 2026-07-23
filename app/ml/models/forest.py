"""Bosques de árboles (Python puro): Random Forest y Extra Trees.

Ambos promedian varios ``DecisionTreeModel``. Random Forest usa muestreo
bootstrap y el mejor umbral por corte; Extra Trees usa la muestra completa y
umbrales aleatorios (más variedad, menos varianza). La importancia de variables
es el promedio de las importancias de los árboles.
"""

import random
from collections.abc import Sequence
from typing import Any

from app.core.exceptions import ModelNotFittedError
from app.ml.interfaces.model import Model, ModelType, as_labels, as_matrix
from app.ml.models.math import mean, normalize
from app.ml.models.tree import DecisionTreeModel


class _ForestModel(Model):
    """Shared bagging logic for tree ensembles."""

    def __init__(
        self,
        *,
        n_estimators: int = 60,
        max_depth: int = 6,
        min_samples_split: int = 6,
        max_features: float = 0.7,
        seed: int = 7,
        bootstrap: bool,
        splitter: str,
    ) -> None:
        super().__init__()
        self._n_estimators = n_estimators
        self._max_depth = max_depth
        self._min_samples_split = min_samples_split
        self._max_features = max_features
        self._seed = seed
        self._bootstrap = bootstrap
        self._splitter = splitter
        self._trees: list[DecisionTreeModel] = []
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        """Whether the ensemble has been trained."""
        return self._fitted

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train ``n_estimators`` trees (bootstrap and/or random splits)."""
        matrix = as_matrix(x)
        labels = as_labels(y)
        self._remember_shape(matrix)
        self._trees = []
        rng = random.Random(self._seed)
        n = len(matrix)
        for i in range(self._n_estimators):
            tree = DecisionTreeModel(
                max_depth=self._max_depth,
                min_samples_split=self._min_samples_split,
                max_features=self._max_features,
                splitter=self._splitter,
                seed=self._seed + i + 1,
            )
            if self._bootstrap and n:
                idx = [rng.randrange(n) for _ in range(n)]
                tree.fit([matrix[j] for j in idx], [labels[j] for j in idx])
            else:
                tree.fit(matrix, labels)
            self._trees.append(tree)
        self._fitted = True

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """Average P(class == 1) across all trees."""
        self._ensure_fitted()
        rows = as_matrix(x)
        per_tree = [tree.predict_proba(rows) for tree in self._trees]
        return [mean([tree_preds[i] for tree_preds in per_tree]) for i in range(len(rows))]

    def feature_importances(self) -> list[float]:
        """Mean per-feature importance across the trees, normalised."""
        self._ensure_fitted()
        if not self._trees:
            return []
        n_features = self._n_features
        totals = [0.0] * n_features
        for tree in self._trees:
            for j, imp in enumerate(tree.feature_importances()):
                totals[j] += imp
        return normalize(totals)

    def params(self) -> dict[str, Any]:
        """Hyperparameters that define the ensemble."""
        return {
            "n_estimators": self._n_estimators,
            "max_depth": self._max_depth,
            "min_samples_split": self._min_samples_split,
            "max_features": self._max_features,
            "bootstrap": self._bootstrap,
            "splitter": self._splitter,
            "seed": self._seed,
        }

    def _ensure_fitted(self) -> None:
        """Raise if a prediction is requested before training."""
        if not self._fitted:
            raise ModelNotFittedError(f"{type(self).__name__} no ha sido entrenado")


class RandomForestModel(_ForestModel):
    """Random Forest: bootstrap sampling + best-threshold splits."""

    def __init__(
        self,
        *,
        n_estimators: int = 60,
        max_depth: int = 6,
        min_samples_split: int = 6,
        max_features: float = 0.7,
        seed: int = 7,
    ) -> None:
        super().__init__(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_split=min_samples_split,
            max_features=max_features,
            seed=seed,
            bootstrap=True,
            splitter="best",
        )

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.RANDOM_FOREST


class ExtraTreesModel(_ForestModel):
    """Extra Trees: whole-sample fit + random-threshold splits."""

    def __init__(
        self,
        *,
        n_estimators: int = 60,
        max_depth: int = 6,
        min_samples_split: int = 6,
        max_features: float = 0.7,
        seed: int = 7,
    ) -> None:
        super().__init__(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_split=min_samples_split,
            max_features=max_features,
            seed=seed,
            bootstrap=False,
            splitter="random",
        )

    @property
    def model_type(self) -> ModelType:
        """Concrete model type."""
        return ModelType.EXTRA_TREES

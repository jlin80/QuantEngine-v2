"""Búsquedas de optimización de parámetros (Fase 6).

Grid Search y Random Search totalmente funcionales sobre un
:class:`ParameterSpace`. El objetivo es una función que recibe un conjunto de
parámetros y devuelve un escalar a maximizar (Sharpe, profit factor, SQN...),
de modo que el optimizador es agnóstico de cómo se evalúa cada combinación.
"""

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.backtesting.optimizer.space import ParameterSpace

Objective = Callable[[dict[str, Any]], float]


@dataclass(frozen=True, slots=True)
class OptimizationTrial:
    """Un intento de la optimización: parámetros y su score."""

    params: dict[str, Any]
    score: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {"params": self.params, "score": round(self.score, 6)}


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Resultado de una optimización."""

    method: str
    objective: str
    best_params: dict[str, Any]
    best_score: float
    evaluations: int
    trials: list[OptimizationTrial]

    def to_dict(self, *, top: int = 10) -> dict[str, Any]:
        """JSON-safe dict.

        Args:
            top: Número de mejores intentos a incluir.

        Returns:
            Diccionario con el mejor resultado y el ranking de intentos.
        """
        ranked = sorted(self.trials, key=lambda t: t.score, reverse=True)[:top]
        return {
            "method": self.method,
            "objective": self.objective,
            "best_params": self.best_params,
            "best_score": round(self.best_score, 6),
            "evaluations": self.evaluations,
            "top_trials": [trial.to_dict() for trial in ranked],
        }


def _finalize(method: str, objective: str, trials: list[OptimizationTrial]) -> OptimizationResult:
    """Build a result from trials, picking the highest score as best."""
    if not trials:
        return OptimizationResult(
            method=method,
            objective=objective,
            best_params={},
            best_score=float("-inf"),
            evaluations=0,
            trials=[],
        )
    best = max(trials, key=lambda t: t.score)
    return OptimizationResult(
        method=method,
        objective=objective,
        best_params=best.params,
        best_score=best.score,
        evaluations=len(trials),
        trials=trials,
    )


class Optimizer(ABC):
    """Base class for parameter optimizers.

    Args:
        objective: Nombre de la métrica que maximiza (informativo).
    """

    method: str = "base"

    def __init__(self, *, objective: str = "score") -> None:
        self._objective = objective

    @abstractmethod
    def optimize(self, space: ParameterSpace, evaluate: Objective) -> OptimizationResult:
        """Search ``space`` maximizing ``evaluate``."""

    @staticmethod
    def _safe_eval(evaluate: Objective, params: dict[str, Any]) -> float:
        """Evaluate a parameter set, treating NaN/inf as the worst score."""
        score = evaluate(params)
        if math.isnan(score) or math.isinf(score):
            return float("-inf")
        return score


class GridSearch(Optimizer):
    """Exhaustive search over the discrete cartesian product of the space."""

    method = "grid"

    def optimize(self, space: ParameterSpace, evaluate: Objective) -> OptimizationResult:
        """Evaluate every discrete combination and keep the best."""
        trials = [
            OptimizationTrial(params=params, score=self._safe_eval(evaluate, params))
            for params in space.grid()
        ]
        return _finalize(self.method, self._objective, trials)


class RandomSearch(Optimizer):
    """Random search over the space (discrete or continuous parameters).

    Args:
        objective: Nombre de la métrica que maximiza.
        max_evaluations: Número de muestras aleatorias.
        seed: Semilla del RNG (reproducibilidad).
    """

    method = "random"

    def __init__(
        self, *, objective: str = "score", max_evaluations: int = 200, seed: int = 7
    ) -> None:
        super().__init__(objective=objective)
        self._max_evaluations = max_evaluations
        self._seed = seed

    def optimize(self, space: ParameterSpace, evaluate: Objective) -> OptimizationResult:
        """Sample the space ``max_evaluations`` times and keep the best."""
        rng = random.Random(self._seed)
        trials = [
            OptimizationTrial(
                params=(params := space.sample(rng)), score=self._safe_eval(evaluate, params)
            )
            for _ in range(self._max_evaluations)
        ]
        return _finalize(self.method, self._objective, trials)

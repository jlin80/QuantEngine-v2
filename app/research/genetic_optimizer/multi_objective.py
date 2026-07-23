"""Optimización multiobjetivo sobre el algoritmo genético (Fase 10)."""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.backtesting.optimizer.advanced import GeneticOptimizer
from app.backtesting.optimizer.space import ParameterSpace
from app.config.settings import MultiObjectiveSettings

MetricsFn = Callable[[dict[str, Any]], dict[str, float]]
"""Evalúa un juego de parámetros y devuelve sus estadísticas (backtest)."""


def _transform(key: str, value: float) -> float:
    """Normalize a raw metric for scalarization (percent metrics → fraction)."""
    if not math.isfinite(value):
        return 0.0
    if key.endswith("_pct"):
        return value / 100.0
    return value


def scalarize(metrics: Mapping[str, float], objectives: Mapping[str, float]) -> float:
    """Weighted-sum scalarization of a metrics dict.

    Args:
        metrics: Estadísticas del backtest (claves reales de ``QuantStatistics``).
        objectives: Pesos por objetivo (positivo maximiza, negativo minimiza).

    Returns:
        El escalar combinado (0.0 si no hay ningún objetivo presente).
    """
    total = 0.0
    for key, weight in objectives.items():
        if key in metrics:
            total += weight * _transform(key, metrics[key])
    return total


def _goodness(metrics: Mapping[str, float], objectives: Mapping[str, float]) -> dict[str, float]:
    """Objective vector oriented so that higher is always better."""
    vector: dict[str, float] = {}
    for key, weight in objectives.items():
        if key in metrics:
            sign = 1.0 if weight >= 0 else -1.0
            vector[key] = sign * _transform(key, metrics[key])
    return vector


def dominates(
    a: Mapping[str, float], b: Mapping[str, float], objectives: Mapping[str, float]
) -> bool:
    """Whether metrics ``a`` Pareto-dominates ``b`` under the objectives.

    ``a`` domina a ``b`` si es al menos tan buena en todos los objetivos y
    estrictamente mejor en al menos uno (tras orientar todo a "más es mejor").
    """
    ga = _goodness(a, objectives)
    gb = _goodness(b, objectives)
    keys = set(ga) & set(gb)
    if not keys:
        return False
    at_least_as_good = all(ga[k] >= gb[k] for k in keys)
    strictly_better = any(ga[k] > gb[k] for k in keys)
    return at_least_as_good and strictly_better


@dataclass(frozen=True, kw_only=True, slots=True)
class ParetoPoint:
    """A non-dominated solution on the Pareto front."""

    params: dict[str, Any]
    metrics: dict[str, float]
    score: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "params": dict(self.params),
            "metrics": {k: round(v, 6) for k, v in self.metrics.items()},
            "score": round(self.score, 6),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class MultiObjectiveResult:
    """Outcome of a multi-objective optimization run."""

    method: str
    objectives: dict[str, float]
    best_params: dict[str, Any]
    best_metrics: dict[str, float]
    best_score: float
    evaluations: int
    pareto_front: tuple[ParetoPoint, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "method": self.method,
            "objectives": dict(self.objectives),
            "best_params": dict(self.best_params),
            "best_metrics": {k: round(v, 6) for k, v in self.best_metrics.items()},
            "best_score": round(self.best_score, 6),
            "evaluations": self.evaluations,
            "pareto_front": [point.to_dict() for point in self.pareto_front],
        }


class MultiObjectiveOptimizer:
    """Multi-objective search driven by the Phase-6 genetic algorithm.

    Args:
        settings: Configuración multiobjetivo (pesos, población, generaciones).
    """

    def __init__(self, settings: MultiObjectiveSettings) -> None:
        self._settings = settings

    def optimize(self, space: ParameterSpace, metrics_fn: MetricsFn) -> MultiObjectiveResult:
        """Evolve parameters maximizing the scalarized multi-objective.

        Args:
            space: Espacio de parámetros a optimizar.
            metrics_fn: Evalúa un juego de parámetros → estadísticas (backtest).

        Returns:
            El mejor punto escalar y el frente de Pareto de lo evaluado.
        """
        objectives = self._settings.objectives
        memo: dict[tuple[tuple[str, Any], ...], dict[str, float]] = {}
        records: list[tuple[dict[str, Any], dict[str, float], float]] = []

        def evaluate(params: dict[str, Any]) -> float:
            key = tuple(sorted(params.items()))
            if key not in memo:
                metrics = metrics_fn(params)
                memo[key] = metrics
                records.append((dict(params), metrics, scalarize(metrics, objectives)))
            return scalarize(memo[key], objectives)

        genetic = GeneticOptimizer(
            objective="multi_objective",
            population_size=self._settings.population_size,
            generations=self._settings.generations,
            mutation_rate=self._settings.mutation_rate,
            seed=self._settings.random_seed,
        )
        genetic.optimize(space, evaluate)

        if not records:
            return MultiObjectiveResult(
                method="multi_objective_genetic",
                objectives=dict(objectives),
                best_params={},
                best_metrics={},
                best_score=float("-inf"),
                evaluations=0,
            )
        best_params, best_metrics, best_score = max(records, key=lambda r: r[2])
        front = _pareto_front(records, objectives)
        return MultiObjectiveResult(
            method="multi_objective_genetic",
            objectives=dict(objectives),
            best_params=best_params,
            best_metrics=best_metrics,
            best_score=best_score,
            evaluations=len(records),
            pareto_front=front,
        )


def _pareto_front(
    records: list[tuple[dict[str, Any], dict[str, float], float]],
    objectives: Mapping[str, float],
) -> tuple[ParetoPoint, ...]:
    """Extract the non-dominated points from all evaluated records."""
    front: list[ParetoPoint] = []
    for params, metrics, score in records:
        if any(dominates(other_metrics, metrics, objectives) for _, other_metrics, _ in records):
            continue
        # Evita duplicados exactos en el frente.
        if any(point.metrics == metrics and point.params == params for point in front):
            continue
        front.append(ParetoPoint(params=params, metrics=metrics, score=score))
    front.sort(key=lambda p: p.score, reverse=True)
    return tuple(front)

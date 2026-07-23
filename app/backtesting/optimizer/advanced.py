"""Optimizadores avanzados (Fase 6).

Incluye un algoritmo genético totalmente funcional y la estructura preparada
para técnicas que dependen de librerías opcionales (optimización bayesiana,
Optuna). Las técnicas no disponibles fallan con un mensaje claro en vez de
fingir un resultado — el laboratorio nunca reporta una optimización que no hizo.
"""

import random
from typing import Any

from app.backtesting.optimizer.search import (
    Objective,
    OptimizationResult,
    OptimizationTrial,
    Optimizer,
    _finalize,
)
from app.backtesting.optimizer.space import ParameterSpace


class OptimizerNotAvailableError(RuntimeError):
    """A prepared optimizer needs an optional dependency not installed."""


def _key(params: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Hashable key for a parameter set (para memorizar evaluaciones)."""
    return tuple(sorted(params.items()))


class GeneticOptimizer(Optimizer):
    """Genetic algorithm over a :class:`ParameterSpace`.

    Selección por elitismo, cruce uniforme y mutación por re-muestreo del
    parámetro. Memoriza las evaluaciones para no repetir backtests caros.

    Args:
        objective: Nombre de la métrica que maximiza.
        population_size: Individuos por generación.
        generations: Número de generaciones.
        mutation_rate: Probabilidad de mutar cada gen.
        seed: Semilla del RNG.
    """

    method = "genetic"

    def __init__(
        self,
        *,
        objective: str = "score",
        population_size: int = 20,
        generations: int = 10,
        mutation_rate: float = 0.15,
        seed: int = 7,
    ) -> None:
        super().__init__(objective=objective)
        self._population_size = max(4, population_size)
        self._generations = max(1, generations)
        self._mutation_rate = mutation_rate
        self._seed = seed

    def optimize(self, space: ParameterSpace, evaluate: Objective) -> OptimizationResult:
        """Evolve a population maximizing ``evaluate``."""
        rng = random.Random(self._seed)
        memo: dict[tuple[tuple[str, Any], ...], float] = {}
        trials: list[OptimizationTrial] = []

        def score_of(params: dict[str, Any]) -> float:
            key = _key(params)
            if key not in memo:
                memo[key] = self._safe_eval(evaluate, params)
                trials.append(OptimizationTrial(params=dict(params), score=memo[key]))
            return memo[key]

        population = [space.sample(rng) for _ in range(self._population_size)]
        for _ in range(self._generations):
            ranked = sorted(population, key=score_of, reverse=True)
            elite_count = max(2, self._population_size // 2)
            elite = ranked[:elite_count]
            population = list(elite)
            while len(population) < self._population_size:
                parent_a = rng.choice(elite)
                parent_b = rng.choice(elite)
                child = self._crossover(parent_a, parent_b, rng)
                child = self._mutate(child, space, rng)
                population.append(child)
        return _finalize(self.method, self._objective, trials)

    @staticmethod
    def _crossover(
        parent_a: dict[str, Any], parent_b: dict[str, Any], rng: random.Random
    ) -> dict[str, Any]:
        """Uniform crossover: each gene comes from one parent at random."""
        return {
            name: (parent_a[name] if rng.random() < 0.5 else parent_b[name]) for name in parent_a
        }

    def _mutate(
        self, child: dict[str, Any], space: ParameterSpace, rng: random.Random
    ) -> dict[str, Any]:
        """Resample each gene with probability ``mutation_rate``."""
        mutated = dict(child)
        for spec in space.specs:
            if rng.random() < self._mutation_rate:
                mutated[spec.name] = spec.sample(rng)
        return mutated


class _PreparedOptimizer(Optimizer):
    """Base for optimizers whose backend is not yet available.

    Args:
        objective: Nombre de la métrica que maximiza.
    """

    dependency: str = "una librería opcional"

    def optimize(self, space: ParameterSpace, evaluate: Objective) -> OptimizationResult:
        """Raise: the backend dependency is not installed.

        Raises:
            OptimizerNotAvailableError: Siempre; requiere la dependencia opcional.
        """
        raise OptimizerNotAvailableError(
            f"El optimizador '{self.method}' requiere {self.dependency}; "
            "estructura preparada, aún no habilitada."
        )


class BayesianOptimizer(_PreparedOptimizer):
    """Bayesian optimization — estructura preparada (requiere backend)."""

    method = "bayesian"
    dependency = "un backend de procesos gaussianos (p. ej. scikit-optimize)"


class OptunaOptimizer(_PreparedOptimizer):
    """Optuna-based optimization — estructura preparada (requiere optuna)."""

    method = "optuna"
    dependency = "la librería optuna"


def build_optimizer(
    method: str,
    *,
    objective: str = "score",
    max_evaluations: int = 200,
    seed: int = 7,
    population_size: int = 20,
    generations: int = 10,
    mutation_rate: float = 0.15,
) -> Optimizer:
    """Build an optimizer by method name.

    Args:
        method: ``grid`` | ``random`` | ``genetic`` | ``bayesian`` | ``optuna``.
        objective: Métrica a maximizar (informativo).
        max_evaluations: Muestras para random.
        seed: Semilla del RNG.
        population_size: Población del genético.
        generations: Generaciones del genético.
        mutation_rate: Tasa de mutación del genético.

    Returns:
        El optimizador solicitado.

    Raises:
        ValueError: Si el método es desconocido.
    """
    # Import diferido para evitar un ciclo con search.py.
    from app.backtesting.optimizer.search import GridSearch, RandomSearch

    if method == "grid":
        return GridSearch(objective=objective)
    if method == "random":
        return RandomSearch(objective=objective, max_evaluations=max_evaluations, seed=seed)
    if method == "genetic":
        return GeneticOptimizer(
            objective=objective,
            population_size=population_size,
            generations=generations,
            mutation_rate=mutation_rate,
            seed=seed,
        )
    if method == "bayesian":
        return BayesianOptimizer(objective=objective)
    if method == "optuna":
        return OptunaOptimizer(objective=objective)
    raise ValueError(f"Método de optimización desconocido: {method}")

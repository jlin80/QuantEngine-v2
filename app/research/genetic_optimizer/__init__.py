"""Genetic Optimizer multiobjetivo (Fase 10).

Envuelve el algoritmo genético de la Fase 6 con una escalarización multiobjetivo
(Profit Factor, Sharpe, Expectancy, SQN, Drawdown...) y conserva el **frente de
Pareto** de las soluciones no dominadas. Nunca optimiza un solo objetivo: eso
produce estrategias frágiles sobreajustadas a una métrica.
"""

from app.research.genetic_optimizer.multi_objective import (
    MultiObjectiveOptimizer,
    MultiObjectiveResult,
    dominates,
    scalarize,
)

__all__ = [
    "MultiObjectiveOptimizer",
    "MultiObjectiveResult",
    "dominates",
    "scalarize",
]

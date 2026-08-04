"""Evaluación continua: resultado virtual de cada señal, por estrategia."""

from app.engine.evaluation.outcomes import (
    VirtualOutcome,
    VirtualOutcomeStore,
    index_outcomes,
)
from app.engine.evaluation.tracker import PerformanceTracker, StrategyPerformance

__all__ = [
    "PerformanceTracker",
    "StrategyPerformance",
    "VirtualOutcome",
    "VirtualOutcomeStore",
    "index_outcomes",
]

"""Laboratorio cuantitativo: backtesting, optimización y validación (Fase 6).

Reutiliza el Execution Engine de la Fase 5 sobre datos históricos con un reloj
de replay (no mantiene dos motores) y añade encima optimización, walk-forward,
Monte Carlo, benchmarking, detección de sobreajuste, experimentos versionados y
un pipeline de calificación de estrategias. Nada de esto habilita live trading:
su única misión es validar estrategias con evidencia estadística antes de paper.

La fachada :class:`~app.backtesting.api.BacktestLab` es el punto de entrada.
"""

from app.backtesting.api import BacktestLab
from app.backtesting.models import BacktestConfig, BacktestResult, EquityPoint

__all__ = ["BacktestConfig", "BacktestLab", "BacktestResult", "EquityPoint"]

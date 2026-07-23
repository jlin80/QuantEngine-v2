"""Ranking Engine (Fase 10): clasifica estrategias y candidatas.

Ordena por un score compuesto multiobjetivo (Profit Factor, Sharpe, Expectancy,
Drawdown, Estabilidad...) y permite segmentar por activo, sesión, régimen o
volatilidad. El ranking es la evidencia que alimenta la promoción.
"""

from app.research.ranking_engine.ranking import RankingEngine

__all__ = ["RankingEngine"]

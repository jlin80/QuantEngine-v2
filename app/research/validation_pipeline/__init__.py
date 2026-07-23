"""Candidate Pipeline (Fase 10): la puerta a "candidata".

Cada estrategia experimental debe superar, en orden, Backtesting → Walk Forward
→ Monte Carlo → Validación ML → Comparación Benchmark → Risk Review. Sólo
entonces se convierte en candidata. Reutiliza el laboratorio de la Fase 6 y
nunca habilita live trading.
"""

from app.research.validation_pipeline.pipeline import CandidatePipeline

__all__ = ["CandidatePipeline"]

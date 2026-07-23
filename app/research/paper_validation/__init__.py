"""Paper Validation (Fase 10): madurez en paper antes de producción.

Ninguna estrategia se promueve de inmediato. Acumula un período configurable en
paper trading y debe cumplir mínimos de evidencia (días, operaciones, profit
factor, drawdown). Es un contador de madurez, no un ejecutor: se alimenta del
paper trading real o de un backtest usado como proxy.
"""

from app.research.paper_validation.tracker import PaperValidationTracker

__all__ = ["PaperValidationTracker"]

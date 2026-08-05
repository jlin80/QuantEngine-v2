"""Edge Research Engine (Bloque 1): mide la salud del edge de cada estrategia."""

from app.engine.edge_research.engine import (
    STATUS_DEGRADING,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT,
    STATUS_WATCH,
    EdgeResearchEngine,
    OutcomesProvider,
)
from app.engine.edge_research.history import EdgeReportHistory
from app.engine.edge_research.models import EdgeResearchReport, StrategyEdgeReport

__all__ = [
    "STATUS_DEGRADING",
    "STATUS_HEALTHY",
    "STATUS_INSUFFICIENT",
    "STATUS_WATCH",
    "EdgeReportHistory",
    "EdgeResearchEngine",
    "EdgeResearchReport",
    "OutcomesProvider",
    "StrategyEdgeReport",
]

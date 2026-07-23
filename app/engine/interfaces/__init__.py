"""Interfaces del núcleo cuantitativo."""

from app.engine.interfaces.consensus import ConsensusAlgorithm
from app.engine.interfaces.filters import SignalFilter
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy

__all__ = ["AnalysisContext", "BaseStrategy", "ConsensusAlgorithm", "SignalFilter"]

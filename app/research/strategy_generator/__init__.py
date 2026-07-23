"""Strategy Generator (Fase 10): construye estrategias experimentales.

Combina bloques de señal (indicadores y order flow) con filtros de contexto
siguiendo reglas cuantitativas —nunca código aleatorio— y las compila a una
``DecisionSource`` reproducible para el motor de backtest. Trabaja siempre sobre
copias: jamás toca las estrategias de producción.
"""

from app.research.strategy_generator.blocks import (
    CONTEXT_FILTERS,
    SIGNAL_BLOCKS,
    BlockSpec,
    FilterSpec,
)
from app.research.strategy_generator.compiler import GenomeDecisionSource, compile_genome
from app.research.strategy_generator.generator import StrategyGenerator

__all__ = [
    "CONTEXT_FILTERS",
    "SIGNAL_BLOCKS",
    "BlockSpec",
    "FilterSpec",
    "GenomeDecisionSource",
    "StrategyGenerator",
    "compile_genome",
]

"""Construcción de espacios de parámetros y source factories (Fase 10)."""

from collections.abc import Callable
from typing import Any

from app.backtesting.optimizer import ParameterSpace
from app.research.models import StrategyGenome
from app.research.strategy_generator.blocks import CONTEXT_FILTERS, SIGNAL_BLOCKS
from app.research.strategy_generator.compiler import GenomeDecisionSource, compile_genome


def build_space(genome: StrategyGenome) -> ParameterSpace:
    """Build the optimizable parameter space for a genome.

    Las claves coinciden con :meth:`StrategyGenome.parameters`, de modo que el
    resultado de un optimizador se aplica directamente con
    :meth:`StrategyGenome.with_parameters`.

    Args:
        genome: Genoma cuyos parámetros se optimizarán.

    Returns:
        El espacio de parámetros con rangos por bloque y filtro.
    """
    space = ParameterSpace()
    for i, block in enumerate(genome.blocks):
        spec = SIGNAL_BLOCKS.get(block.kind)
        if spec is None:
            continue
        for name, (low, high, is_int) in spec.ranges.items():
            space.add_range(f"b{i}_{name}", low, high, is_int=is_int)
    for i, filt in enumerate(genome.filters):
        fspec = CONTEXT_FILTERS.get(filt.kind)
        if fspec is None:
            continue
        for name, (low, high, is_int) in fspec.ranges.items():
            space.add_range(f"f{i}_{name}", low, high, is_int=is_int)
    return space


def build_source_factory(
    genome: StrategyGenome,
) -> Callable[[dict[str, Any]], GenomeDecisionSource]:
    """Return a factory that compiles the genome with overridden parameters.

    Args:
        genome: Genoma base (nunca se muta: se clona por cada evaluación).

    Returns:
        Callable ``params -> DecisionSource`` para optimizador/walk-forward.
    """

    def factory(params: dict[str, Any]) -> GenomeDecisionSource:
        flat = {key: float(value) for key, value in params.items()}
        return compile_genome(genome.with_parameters(flat))

    return factory


def space_size(genome: StrategyGenome) -> int:
    """Number of tunable parameters in the genome's space."""
    return len(build_space(genome).names)

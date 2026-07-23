"""Compilador de genomas a ``DecisionSource`` (Fase 10).

Convierte un :class:`~app.research.models.StrategyGenome` (datos) en una fuente
de decisiones ejecutable por el motor de backtest de la Fase 6. Combina los
bloques según el modo del genoma, aplica los filtros de contexto y emite una
apertura sólo cuando la dirección resultante cambia (evita señales repetidas).
No genera ni evalúa código: sólo despacha predicados auditados del catálogo.
"""

from collections.abc import Sequence

from app.backtesting.decisions import open_decision
from app.core.exceptions import StrategyGenerationError
from app.engine.events import DecisionGenerated
from app.engine.models import Direction
from app.market.models import Candle
from app.research.models import Combine, StrategyGenome
from app.research.strategy_generator.blocks import (
    CONTEXT_FILTERS,
    SIGNAL_BLOCKS,
    BlockSpec,
    FilterSpec,
)


class GenomeDecisionSource:
    """A compiled genome that decides per bar (implements ``DecisionSource``).

    Args:
        genome: Genoma a compilar.

    Raises:
        StrategyGenerationError: Si el genoma referencia un bloque/filtro
            desconocido, o no tiene bloques.
    """

    def __init__(self, genome: StrategyGenome) -> None:
        if not genome.blocks:
            raise StrategyGenerationError(
                "El genoma no tiene bloques de señal", context={"genome": genome.id}
            )
        self._genome = genome
        self._blocks: list[tuple[BlockSpec, dict[str, float]]] = []
        for block in genome.blocks:
            spec = SIGNAL_BLOCKS.get(block.kind)
            if spec is None:
                raise StrategyGenerationError(
                    f"Bloque de señal desconocido: {block.kind}",
                    context={"genome": genome.id, "kind": block.kind},
                )
            self._blocks.append((spec, {**spec.defaults, **block.params}))
        self._filters: list[tuple[FilterSpec, dict[str, float]]] = []
        for filt in genome.filters:
            fspec = CONTEXT_FILTERS.get(filt.kind)
            if fspec is None:
                raise StrategyGenerationError(
                    f"Filtro de contexto desconocido: {filt.kind}",
                    context={"genome": genome.id, "kind": filt.kind},
                )
            self._filters.append((fspec, {**fspec.defaults, **filt.params}))
        self._last_side: str | None = None

    @property
    def genome(self) -> StrategyGenome:
        """The genome this source was compiled from."""
        return self._genome

    def reset(self) -> None:
        """Forget the last emitted side before a fresh run."""
        self._last_side = None

    def decide(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        """Combine blocks and filters into an open decision (or ``None``)."""
        if index < 0 or index >= len(candles):
            return None
        for fspec, fparams in self._filters:
            if not fspec.predicate(fparams, candles, index):
                return None  # el contexto veta: no se abre nada
        direction = self._combine(candles, index)
        if direction is Direction.NEUTRAL:
            return None
        side = "up" if direction is Direction.LONG else "down"
        if side == self._last_side:
            return None
        self._last_side = side
        if direction is Direction.LONG:
            return open_decision(symbol, "open_long", summary=f"{self._genome.name} long")
        if self._genome.allow_short:
            return open_decision(symbol, "open_short", summary=f"{self._genome.name} short")
        return None

    def _combine(self, candles: Sequence[Candle], index: int) -> Direction:
        """Aggregate block directions per the genome's combine mode."""
        longs = 0
        shorts = 0
        for spec, params in self._blocks:
            direction = spec.predicate(params, candles, index)
            if direction is Direction.LONG:
                longs += 1
            elif direction is Direction.SHORT:
                shorts += 1
        active = longs + shorts
        if active == 0:
            return Direction.NEUTRAL
        if self._genome.combine is Combine.ALL:
            if longs == len(self._blocks):
                return Direction.LONG
            if shorts == len(self._blocks):
                return Direction.SHORT
            return Direction.NEUTRAL
        if self._genome.combine is Combine.ANY:
            if longs > 0 and shorts == 0:
                return Direction.LONG
            if shorts > 0 and longs == 0:
                return Direction.SHORT
            return Direction.NEUTRAL
        # MAJORITY
        if longs > shorts:
            return Direction.LONG
        if shorts > longs:
            return Direction.SHORT
        return Direction.NEUTRAL


def compile_genome(genome: StrategyGenome) -> GenomeDecisionSource:
    """Compile a genome into an executable decision source.

    Args:
        genome: Genoma a compilar.

    Returns:
        La fuente de decisiones lista para el motor de backtest.
    """
    return GenomeDecisionSource(genome)

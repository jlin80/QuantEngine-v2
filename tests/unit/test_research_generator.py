"""Strategy Generator, compiler y parameter lab (Fase 10)."""

import itertools

from app.config.settings import StrategyGeneratorSettings
from app.core.exceptions import StrategyGenerationError
from app.research.models import Combine, SignalBlock, StrategyGenome, new_id
from app.research.parameter_lab import build_source_factory, build_space, space_size
from app.research.strategy_generator import (
    CONTEXT_FILTERS,
    SIGNAL_BLOCKS,
    StrategyGenerator,
    compile_genome,
)
from app.research.strategy_generator.generator import _POLARITY

from tests.unit.research_helpers import candles, filtered_genome, trend_genome


def _generator(**overrides) -> StrategyGenerator:
    return StrategyGenerator(StrategyGeneratorSettings(**overrides))


def test_generation_is_deterministic_and_deduplicated():
    gen = _generator(population=10, random_seed=3)
    a = gen.generate("BTCUSDT", "1m")
    b = gen.generate("BTCUSDT", "1m")
    assert [g.signature() for g in a] == [g.signature() for g in b]  # reproducible
    assert len({g.signature() for g in a}) == len(a)  # deduplicado


def test_generated_genomes_respect_polarity_coherence():
    gen = _generator(population=40, random_seed=1, max_blocks=3)
    for genome in gen.generate("ETHUSDT", "1m"):
        polarities = {_POLARITY[SIGNAL_BLOCKS[b.kind].category] for b in genome.blocks}
        assert len(polarities) == 1  # nunca mezcla seguir-tendencia con reversión


def test_generated_filters_are_affinity_compatible():
    gen = _generator(population=30, random_seed=2, max_filters=2)
    for genome in gen.generate("BTCUSDT", "1m"):
        for filt in genome.filters:
            assert filt.kind in CONTEXT_FILTERS


def test_generator_respects_block_and_filter_limits():
    gen = _generator(population=15, max_blocks=2, max_filters=1, random_seed=5)
    for genome in gen.generate("BTCUSDT", "1m"):
        assert 1 <= len(genome.blocks) <= 2
        assert len(genome.filters) <= 1


def test_compile_and_decide_emits_on_flip_only():
    genome = trend_genome(fast=3, slow=10)
    source = compile_genome(genome)
    source.reset()
    data = candles(300, drift=0.0009, seed=4)
    decisions = [source.decide("BTCUSDT", data, i) for i in range(len(data))]
    opens = [d for d in decisions if d is not None]
    assert opens, "debería emitir al menos una decisión"
    # nunca dos aperturas idénticas consecutivas (emite sólo en el cambio de lado)
    actions = [d.action for d in opens]
    assert all(a != b for a, b in itertools.pairwise(actions)) or len(actions) == 1


def test_compile_rejects_unknown_block():
    genome = StrategyGenome(
        id=new_id("g"),
        name="bad",
        symbol="BTCUSDT",
        timeframe="1m",
        blocks=(SignalBlock(kind="does_not_exist", params={}),),
    )
    try:
        compile_genome(genome)
        raise AssertionError("debió fallar con bloque desconocido")
    except StrategyGenerationError:
        pass


def test_compile_rejects_empty_genome():
    genome = StrategyGenome(
        id=new_id("g"), name="empty", symbol="BTCUSDT", timeframe="1m", blocks=()
    )
    try:
        compile_genome(genome)
        raise AssertionError("debió fallar con genoma vacío")
    except StrategyGenerationError:
        pass


def test_no_short_genome_never_emits_short():
    genome = StrategyGenome(
        id=new_id("g"),
        name="long_only",
        symbol="BTCUSDT",
        timeframe="1m",
        blocks=(SignalBlock(kind="ema_cross", params={"fast": 3.0, "slow": 10.0}),),
        combine=Combine.ALL,
        allow_short=False,
    )
    source = compile_genome(genome)
    source.reset()
    data = candles(300, seed=6)
    actions = {d.action for d in (source.decide("BTCUSDT", data, i) for i in range(len(data))) if d}
    assert "open_short" not in actions


def test_parameter_space_keys_match_flatten():
    genome = filtered_genome()
    space = build_space(genome)
    assert set(space.names) == set(genome.parameters())
    assert space_size(genome) == len(genome.parameters())


def test_source_factory_applies_overrides_without_mutating_original():
    genome = trend_genome(fast=5, slow=20)
    original = genome.parameters()
    factory = build_source_factory(genome)
    source = factory({"b0_fast": 8.0, "b0_slow": 30.0})
    assert source.genome.parameters()["b0_fast"] == 8.0
    assert genome.parameters() == original  # el genoma base no se muta

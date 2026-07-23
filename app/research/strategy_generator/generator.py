"""Generador automático de estrategias (Fase 10).

Construye genomas combinando bloques de señal y filtros de contexto **siguiendo
reglas cuantitativas**: coherencia de polaridad (no mezcla seguir-tendencia con
reversión en una misma combinación), afinidad de filtros por familia y muestreo
reproducible de parámetros dentro de dominios auditados. Nunca genera código
arbitrario ni toca las estrategias de producción.
"""

import random
from collections.abc import Sequence

from app.config.settings import StrategyGeneratorSettings
from app.research.models import Combine, ContextFilter, SignalBlock, StrategyGenome, new_id
from app.research.strategy_generator.blocks import (
    CONTEXT_FILTERS,
    FILTER_AFFINITY,
    SIGNAL_BLOCKS,
    FilterSpec,
    Range,
)

# Polaridad de cada familia: seguir-tendencia vs contra-tendencia. Sólo se
# combinan bloques de la misma polaridad para que las señales no se anulen.
_POLARITY: dict[str, str] = {
    "trend": "with",
    "breakout": "with",
    "momentum": "with",
    "orderflow": "with",
    "reversion": "counter",
}


class StrategyGenerator:
    """Rule-based generator of experimental strategy genomes.

    Args:
        settings: Configuración del generador (bloques/filtros por estrategia,
            población, semilla, si permite cortos).
    """

    def __init__(self, settings: StrategyGeneratorSettings) -> None:
        self._settings = settings

    def generate(
        self, symbol: str, timeframe: str, *, count: int | None = None, seed: int | None = None
    ) -> list[StrategyGenome]:
        """Generate a de-duplicated batch of genomes.

        Args:
            symbol: Símbolo objetivo.
            timeframe: Timeframe objetivo.
            count: Número de genomas (por defecto ``settings.population``).
            seed: Semilla base (por defecto ``settings.random_seed``).

        Returns:
            Genomas únicos por estructura+parámetros.
        """
        n = count if count is not None else self._settings.population
        base_seed = seed if seed is not None else self._settings.random_seed
        seen: set[str] = set()
        out: list[StrategyGenome] = []
        # Se intenta con un margen amplio: el dedup por firma puede descartar.
        for i in range(n * 4):
            if len(out) >= n:
                break
            genome = self.generate_one(symbol, timeframe, seed=base_seed + i)
            signature = genome.signature()
            if signature in seen:
                continue
            seen.add(signature)
            out.append(genome)
        return out

    def generate_one(self, symbol: str, timeframe: str, *, seed: int) -> StrategyGenome:
        """Generate a single genome with a specific seed."""
        rng = random.Random(seed)
        theme = rng.choice(sorted({spec.category for spec in SIGNAL_BLOCKS.values()}))
        polarity = _POLARITY[theme]
        candidates = [
            spec for spec in SIGNAL_BLOCKS.values() if _POLARITY[spec.category] == polarity
        ]
        max_blocks = min(self._settings.max_blocks, len(candidates))
        min_blocks = max(1, min(self._settings.min_blocks, max_blocks))
        n_blocks = rng.randint(min_blocks, max_blocks)
        chosen = rng.sample(candidates, n_blocks)
        blocks = tuple(
            SignalBlock(kind=spec.kind, params=_sample_params(spec.ranges, rng)) for spec in chosen
        )

        combine = Combine.ALL if n_blocks == 1 else rng.choice(list(Combine))
        filters = self._pick_filters(theme, rng)
        allow_short = self._settings.allow_short and rng.random() < 0.85

        kinds = "+".join(spec.kind for spec in chosen)
        name = f"{kinds}::{combine.value}"
        return StrategyGenome(
            id=new_id("gen"),
            name=name,
            symbol=symbol.upper(),
            timeframe=timeframe,
            blocks=blocks,
            filters=filters,
            combine=combine,
            allow_short=allow_short,
            seed=seed,
            metadata={"theme": theme, "polarity": polarity},
        )

    def _pick_filters(self, theme: str, rng: random.Random) -> tuple[ContextFilter, ...]:
        """Pick affinity-compatible context filters for a theme."""
        affinity = list(FILTER_AFFINITY.get(theme, ()))
        if not affinity or self._settings.max_filters <= 0:
            return ()
        rng.shuffle(affinity)
        n_filters = rng.randint(0, min(self._settings.max_filters, len(affinity)))
        picked: list[ContextFilter] = []
        for kind in affinity[:n_filters]:
            spec: FilterSpec = CONTEXT_FILTERS[kind]
            picked.append(ContextFilter(kind=kind, params=_sample_params(spec.ranges, rng)))
        return tuple(picked)

    @staticmethod
    def catalog() -> dict[str, object]:
        """Describe the available building blocks (for the dashboard)."""
        return {
            "blocks": [
                {
                    "kind": spec.kind,
                    "category": spec.category,
                    "needs_orderflow": spec.needs_orderflow,
                }
                for spec in SIGNAL_BLOCKS.values()
            ],
            "filters": [
                {"kind": spec.kind, "category": spec.category} for spec in CONTEXT_FILTERS.values()
            ],
            "combine_modes": [mode.value for mode in Combine],
        }


def _sample_params(ranges: dict[str, Range], rng: random.Random) -> dict[str, float]:
    """Sample each parameter uniformly within its declared domain."""
    params: dict[str, float] = {}
    for name, (low, high, is_int) in ranges.items():
        if is_int:
            params[name] = float(rng.randint(int(low), int(high)))
        else:
            params[name] = round(rng.uniform(low, high), 6)
    return params


def _all_block_kinds() -> Sequence[str]:
    """Every registered signal-block kind (helper for tests/tools)."""
    return tuple(SIGNAL_BLOCKS)

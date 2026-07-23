"""Motor de consenso: algoritmo intercambiable por configuración."""

import logging
from collections.abc import Callable, Mapping, Sequence

from app.config.settings import QuantConsensusSettings
from app.core.exceptions import ConfigurationError
from app.engine.consensus.algorithms import (
    DynamicWeighting,
    MajorityVoting,
    RegimeWeighting,
    WeightedAverage,
    WeightedVoting,
)
from app.engine.interfaces.consensus import ConsensusAlgorithm
from app.engine.models import ConsensusResult, MarketContext, StrategySignal


class ConsensusEngine:
    """Builds consensus with a configurable, swappable algorithm.

    Args:
        settings: Método activo, umbrales y multiplicadores por régimen.
        weights: Peso base por estrategia (configurable en caliente).
        performance_factor: Rendimiento reciente por estrategia (0-1).
    """

    def __init__(
        self,
        settings: QuantConsensusSettings,
        weights: Mapping[str, float] | None = None,
        performance_factor: Callable[[str], float] | None = None,
    ) -> None:
        self._settings = settings
        self._weights: dict[str, float] = dict(weights or {})
        self._log = logging.getLogger("app.engine.consensus")
        default = settings.default_weight
        self._algorithms: dict[str, ConsensusAlgorithm] = {}
        for algorithm in (
            MajorityVoting(),
            WeightedVoting(default_weight=default),
            WeightedAverage(default_weight=default),
            DynamicWeighting(performance_factor, default_weight=default),
            RegimeWeighting(settings.regime_multipliers, default_weight=default),
        ):
            self._algorithms[algorithm.name] = algorithm
        if settings.method not in self._algorithms:
            raise ConfigurationError(
                f"Unknown consensus method '{settings.method}'",
                context={"available": sorted(self._algorithms)},
            )
        self._method = settings.method

    @property
    def method(self) -> str:
        """Active algorithm name."""
        return self._method

    @property
    def available_methods(self) -> list[str]:
        """Every registered algorithm."""
        return sorted(self._algorithms)

    def set_method(self, name: str) -> None:
        """Swap the active algorithm.

        Raises:
            ConfigurationError: Si el método no existe.
        """
        if name not in self._algorithms:
            raise ConfigurationError(
                f"Unknown consensus method '{name}'",
                context={"available": sorted(self._algorithms)},
            )
        self._method = name
        self._log.info("Consensus method switched to '%s'", name)

    def register(self, algorithm: ConsensusAlgorithm) -> None:
        """Add a custom algorithm."""
        self._algorithms[algorithm.name] = algorithm

    def set_weight(self, strategy: str, weight: float) -> None:
        """Update the base weight of a strategy."""
        self._weights[strategy] = max(0.0, weight)

    @property
    def weights(self) -> dict[str, float]:
        """Current base weights."""
        return dict(self._weights)

    def build(
        self,
        symbol: str,
        signals: Sequence[StrategySignal],
        context: MarketContext | None = None,
    ) -> ConsensusResult:
        """Run the active algorithm over a symbol's signals."""
        algorithm = self._algorithms[self._method]
        return algorithm.build(symbol, signals, self._weights, context)

    def status(self) -> dict[str, object]:
        """Diagnostic snapshot for the dashboard."""
        return {
            "method": self._method,
            "available": self.available_methods,
            "weights": dict(self._weights),
            "min_signals": self._settings.min_signals,
            "min_score": self._settings.min_score,
            "min_confidence": self._settings.min_confidence,
            "min_agreement": self._settings.min_agreement,
        }

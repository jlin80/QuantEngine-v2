"""Contrato de los algoritmos de consenso."""

import abc
from collections.abc import Mapping, Sequence

from app.engine.models import ConsensusResult, MarketContext, StrategySignal


class ConsensusAlgorithm(abc.ABC):
    """Algoritmo intercambiable que agrega señales en un consenso."""

    name: str = "base"

    @abc.abstractmethod
    def build(
        self,
        symbol: str,
        signals: Sequence[StrategySignal],
        weights: Mapping[str, float],
        context: MarketContext | None = None,
    ) -> ConsensusResult:
        """Aggregate signals into a single consensus.

        Args:
            symbol: Símbolo evaluado.
            signals: Señales activas del símbolo.
            weights: Peso por estrategia (default 1.0 si no aparece).
            context: Contexto de mercado (algunos algoritmos lo usan).

        Returns:
            Resultado de consenso con dirección, score global y acuerdo.
        """

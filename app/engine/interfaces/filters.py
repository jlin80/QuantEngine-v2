"""Contrato de los filtros previos a una decisión."""

import abc

from app.engine.models import ConsensusResult, FilterResult, MarketContext


class SignalFilter(abc.ABC):
    """Filtro independiente que puede vetar una posible operación.

    Args:
        name: Identificador del filtro.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abc.abstractmethod
    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Evaluate the filter against the current context/consensus.

        Args:
            context: Contexto de mercado del símbolo.
            consensus: Consenso alcanzado (dirección/score/acuerdo).

        Returns:
            Resultado (``passed=False`` bloquea, con razón obligatoria).
        """

"""Resultado de un intento de ejecución, común a cualquier broker.

Este modelo era interno del Paper Engine (Fase 5). La Fase 9 lo promueve a
modelo de dominio para que el ``ExecutionBroker`` de ``app/core/interfaces``
tenga un tipo de retorno que no dependa de una implementación concreta.
"""

from dataclasses import dataclass

from app.execution.models.enums import RejectReason
from app.execution.models.orders import Fill


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    """Outcome of asking a broker to execute an order.

    Attributes:
        fill: Resulting fill, or ``None`` when the order was rejected.
        reject_reason: Why the order was rejected (``NONE`` when accepted).
    """

    fill: Fill | None
    reject_reason: RejectReason = RejectReason.NONE

    @property
    def accepted(self) -> bool:
        """Whether the order produced a (possibly partial) fill."""
        return self.fill is not None

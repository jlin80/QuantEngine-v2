"""Contrato de brokers de ejecución.

El Execution Engine opera exclusivamente contra esta interfaz, nunca contra una
implementación concreta. En esta versión la única implementación registrada es
el ``PaperBroker`` (simulación); la selección la hace
``app.production.live.broker_selector``, que sólo puede devolver un broker real
si el Live Gate aprueba — cosa que hoy no puede ocurrir.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.execution.models import BrokerExecution, OrderRequest
    from app.execution.slippage import SlippageContext
    from app.market.models import Ticker


@runtime_checkable
class ExecutionBroker(Protocol):
    """Venue capable of executing orders (simulated or real)."""

    @property
    def broker_name(self) -> str:
        """Short broker identifier (e.g. ``paper``)."""
        ...

    @property
    def stats(self) -> dict[str, int]:
        """Execution counters (executed/rejected)."""
        ...

    def execute(
        self,
        request: "OrderRequest",
        ticker: "Ticker",
        context: "SlippageContext | None" = None,
        *,
        allow_reject: bool = True,
        allow_partial: bool = True,
    ) -> "BrokerExecution":
        """Attempt to execute an order against the current market.

        Args:
            request: Trading intent.
            ticker: Best bid/ask for the symbol.
            context: Slippage context (volatility, liquidity, session).
            allow_reject: Whether rejection is allowed. Exits disable it so a
                close never gets stuck.
            allow_partial: Whether partial fills are allowed.

        Returns:
            The fill, or the reason the order was rejected.
        """
        ...

    def healthcheck(self) -> bool:
        """Return whether the broker is usable right now.

        The Live Gate consumes this as its "broker connected" criterion.
        """
        ...

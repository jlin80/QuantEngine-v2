"""Métricas de microestructura (Bloque 3).

Todas se derivan del **libro de órdenes incremental**: no del precio, no de la
vela. Esa dependencia es la clave para leer este módulo, y por eso el snapshot
lleva ``observable`` y ``reason``: con un proveedor que no publica libro —el
caso de MT5, el broker de la demo, documentado en ``docs/orderflow_nativo.md``—
ninguna de estas métricas existe. Devolver ceros ahí sería fabricar
microestructura, y un cero de "queue imbalance" es indistinguible de un libro
perfectamente equilibrado.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class MicrostructureSnapshot:
    """Estado de microestructura de un símbolo en un instante.

    Attributes:
        symbol: Activo.
        at: Momento del cálculo.
        observable: Si hay libro sincronizado y muestra suficiente. Cuando es
            ``False`` **todas** las métricas son ``None``.
        reason: Por qué no es observable (vacío si lo es).
        queue_imbalance: Desequilibrio de tamaño en el top del libro, -1..1
            (positivo = presión compradora).
        queue_ahead: Tamaño mostrado en el mejor precio del lado a ejecutar; es
            la cola por delante de una orden límite nueva.
        order_arrival_rate: Tamaño añadido al libro por segundo.
        cancel_rate: Tamaño retirado del libro por segundo **sin operación de
            por medio**: cancelaciones, no ejecuciones.
        book_resiliency: Reposición dividida entre consumo, 0..∞ (1.0 = el libro
            repone exactamente lo que se le come).
        replenishment: Tamaño repuesto en el top por segundo.
        liquidity_consumption: Tamaño consumido por operaciones por segundo.
        market_impact_bps: Coste estimado, en puntos básicos, de barrer el libro
            con el nocional de referencia configurado.
        execution_pressure: Resumen 0..1 de lo hostil que está el libro para
            ejecutar ahora mismo.
        updates: Actualizaciones de libro que sostienen la medición.
    """

    symbol: str
    at: datetime = field(default_factory=utc_now)
    observable: bool = False
    reason: str = ""
    queue_imbalance: float | None = None
    queue_ahead: float | None = None
    order_arrival_rate: float | None = None
    cancel_rate: float | None = None
    book_resiliency: float | None = None
    replenishment: float | None = None
    liquidity_consumption: float | None = None
    market_impact_bps: float | None = None
    execution_pressure: float | None = None
    updates: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "at": self.at.isoformat(),
            "observable": self.observable,
            "reason": self.reason,
            "queue_imbalance": _round(self.queue_imbalance),
            "queue_ahead": _round(self.queue_ahead),
            "order_arrival_rate": _round(self.order_arrival_rate),
            "cancel_rate": _round(self.cancel_rate),
            "book_resiliency": _round(self.book_resiliency),
            "replenishment": _round(self.replenishment),
            "liquidity_consumption": _round(self.liquidity_consumption),
            "market_impact_bps": _round(self.market_impact_bps),
            "execution_pressure": _round(self.execution_pressure),
            "updates": self.updates,
        }


def _round(value: float | None, digits: int = 6) -> float | None:
    """Round without turning ``None`` into a number."""
    return None if value is None else round(value, digits)

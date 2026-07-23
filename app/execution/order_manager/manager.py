"""Order Manager: registra y sigue el estado de todas las órdenes.

Mantiene el histórico de órdenes, expone las activas y prepara la estructura
OCO (one-cancels-the-other) para fases futuras. No ejecuta: la ejecución la
hace el Paper Engine; aquí sólo vive el estado.
"""

from collections import deque
from dataclasses import dataclass, field

from app.execution.models import Fill, Order, OrderRequest, OrderStatus, RejectReason


@dataclass(slots=True)
class OcoGroup:
    """Grupo OCO: al ejecutarse una orden, la otra se cancela (preparado)."""

    group_id: str
    order_ids: list[str] = field(default_factory=list)
    active: bool = True


class OrderManager:
    """Track the lifecycle of every order created by the engine.

    Args:
        history_limit: Máximo de órdenes retenidas en memoria.
    """

    def __init__(self, *, history_limit: int = 5_000) -> None:
        self._orders: dict[str, Order] = {}
        self._history: deque[str] = deque(maxlen=history_limit)
        self._oco: dict[str, OcoGroup] = {}
        self._created = 0
        self._filled = 0
        self._rejected = 0
        self._cancelled = 0

    def create(self, request: OrderRequest) -> Order:
        """Register a new order for a request."""
        order = Order(request=request, status=OrderStatus.PENDING)
        self._orders[order.order_id] = order
        self._history.append(order.order_id)
        self._created += 1
        return order

    def get(self, order_id: str) -> Order | None:
        """Return an order by id (or ``None``)."""
        return self._orders.get(order_id)

    def apply_fill(self, order: Order, fill: Fill) -> None:
        """Apply a fill to an order and update counters/OCO links."""
        order.apply_fill(fill)
        if order.status is OrderStatus.FILLED:
            self._filled += 1
            self._cancel_oco_siblings(order.order_id)

    def reject(self, order: Order, reason: RejectReason) -> None:
        """Mark an order as rejected."""
        order.reject(reason)
        self._rejected += 1

    def cancel(self, order: Order) -> None:
        """Cancel an order if it is not terminal yet."""
        if not order.is_terminal:
            order.status = OrderStatus.CANCELLED
            self._cancelled += 1

    def create_oco(self, order_ids: list[str], group_id: str) -> OcoGroup:
        """Register an OCO group linking sibling orders (estructura preparada)."""
        group = OcoGroup(group_id=group_id, order_ids=list(order_ids))
        self._oco[group_id] = group
        return group

    def _cancel_oco_siblings(self, filled_order_id: str) -> None:
        """Cancel the siblings of a filled OCO order."""
        for group in self._oco.values():
            if group.active and filled_order_id in group.order_ids:
                group.active = False
                for other_id in group.order_ids:
                    if other_id != filled_order_id:
                        sibling = self._orders.get(other_id)
                        if sibling is not None:
                            self.cancel(sibling)

    @property
    def active_orders(self) -> list[Order]:
        """Orders that are not in a terminal state."""
        return [o for o in self._orders.values() if not o.is_terminal]

    def recent(self, limit: int = 50) -> list[Order]:
        """Most recent orders (newest last)."""
        ids = list(self._history)[-limit:]
        return [self._orders[i] for i in ids if i in self._orders]

    def status(self) -> dict[str, int]:
        """Order lifecycle counters."""
        return {
            "created": self._created,
            "filled": self._filled,
            "rejected": self._rejected,
            "cancelled": self._cancelled,
            "active": len(self.active_orders),
        }

"""Modelos de órdenes y ejecuciones (fills).

Una orden es una intención inmutable; su estado vive en el Order Manager.
Un fill es el resultado atómico de una ejecución (paper) con todos sus costes
desglosados: precio de referencia, precio ejecutado, slippage, comisión,
latencia y spread. Nada se asume "perfecto".
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.execution.models.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    RejectReason,
    TimeInForce,
)
from app.utils.time import utc_now


def _new_id() -> str:
    """Unique identifier for orders/fills/trades."""
    return uuid.uuid4().hex


def _iso(moment: datetime | None) -> str | None:
    """ISO-8601 or ``None`` passthrough."""
    return None if moment is None else moment.isoformat()


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderRequest:
    """Intención de operar emitida por el Execution Engine.

    Attributes:
        request_id: Identificador único de la intención.
        symbol: Símbolo objetivo.
        side: Compra o venta.
        quantity: Cantidad (unidades del activo).
        order_type: Tipo de orden.
        time_in_force: Vigencia.
        limit_price: Precio límite (LIMIT / STOP_LIMIT).
        stop_price: Precio de disparo (STOP_*).
        stop_loss: Stop de protección de la posición resultante.
        take_profit: Objetivo de la posición resultante.
        reduce_only: Si solo puede reducir/cerrar posición.
        decision_id: Decisión que la originó (trazabilidad).
        signal_ids: Señales que el consenso consideró en esa decisión. Se
            arrastran para poder unir la operación resultante con el resultado
            virtual que el evaluador continuo calcula por señal.
        reason: Motivo de la orden (explicabilidad).
        metadata: Datos adicionales JSON-safe.
    """

    request_id: str = field(default_factory=_new_id)
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.GTC
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reduce_only: bool = False
    decision_id: str | None = None
    signal_ids: tuple[str, ...] = ()
    reason: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "request_id": self.request_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reduce_only": self.reduce_only,
            "decision_id": self.decision_id,
            "signal_ids": list(self.signal_ids),
            "reason": self.reason,
            "created_at": _iso(self.created_at),
            "metadata": self.metadata,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Fill:
    """Resultado de una ejecución simulada (paper).

    Attributes:
        fill_id: Identificador del fill.
        request_id: Orden que lo originó.
        symbol: Símbolo.
        side: Lado ejecutado.
        quantity: Cantidad ejecutada (puede ser parcial).
        requested_quantity: Cantidad solicitada.
        reference_price: Precio de referencia antes de costes (bid/ask/mid).
        price: Precio finalmente ejecutado (con slippage y latencia).
        slippage_bps: Slippage aplicado en puntos básicos.
        spread_bps: Spread del mercado en el momento del fill.
        commission: Comisión cobrada por el fill.
        latency_ms: Latencia total simulada.
        liquidity: ``maker`` o ``taker``.
        gapped: Si el precio saltó (gap) respecto a la referencia.
        executed_at: Momento de ejecución (UTC).
        broker_ref: Identificador de la operación en el broker real (p. ej. el
            ticket de posición en MT5), si aplica. ``None`` en paper trading.
    """

    fill_id: str = field(default_factory=_new_id)
    request_id: str
    symbol: str
    side: OrderSide
    quantity: float
    requested_quantity: float
    reference_price: float
    price: float
    slippage_bps: float = 0.0
    spread_bps: float = 0.0
    commission: float = 0.0
    latency_ms: float = 0.0
    liquidity: str = "taker"
    gapped: bool = False
    executed_at: datetime = field(default_factory=utc_now)
    broker_ref: str | None = None

    @property
    def notional(self) -> float:
        """Valor nocional ejecutado."""
        return self.quantity * self.price

    @property
    def is_partial(self) -> bool:
        """Whether the fill covered less than the requested quantity."""
        return self.quantity + 1e-12 < self.requested_quantity

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "fill_id": self.fill_id,
            "request_id": self.request_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "requested_quantity": self.requested_quantity,
            "reference_price": self.reference_price,
            "price": self.price,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
            "commission": self.commission,
            "latency_ms": self.latency_ms,
            "liquidity": self.liquidity,
            "gapped": self.gapped,
            "notional": self.notional,
            "is_partial": self.is_partial,
            "executed_at": _iso(self.executed_at),
        }


@dataclass(kw_only=True, slots=True)
class Order:
    """Orden con estado mutable, gestionada por el Order Manager.

    Attributes:
        order_id: Identificador de la orden.
        request: Intención inmutable de origen.
        status: Estado actual.
        filled_quantity: Cantidad ejecutada acumulada.
        average_price: Precio medio de ejecución.
        reject_reason: Motivo de rechazo (si aplica).
        fills: Fills asociados.
        created_at: Alta de la orden.
        updated_at: Última actualización.
    """

    order_id: str = field(default_factory=_new_id)
    request: OrderRequest
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: float = 0.0
    average_price: float = 0.0
    reject_reason: RejectReason = RejectReason.NONE
    fills: list[Fill] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def symbol(self) -> str:
        """Symbol of the underlying request."""
        return self.request.symbol

    @property
    def side(self) -> OrderSide:
        """Side of the underlying request."""
        return self.request.side

    @property
    def is_terminal(self) -> bool:
        """Whether the order reached a final state."""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        )

    @property
    def remaining_quantity(self) -> float:
        """Quantity still to be filled."""
        return max(0.0, self.request.quantity - self.filled_quantity)

    def apply_fill(self, fill: Fill) -> None:
        """Register a fill and update the aggregate state.

        Args:
            fill: Ejecución a incorporar.
        """
        prev_notional = self.average_price * self.filled_quantity
        self.fills.append(fill)
        self.filled_quantity += fill.quantity
        if self.filled_quantity > 0:
            self.average_price = (prev_notional + fill.price * fill.quantity) / self.filled_quantity
        self.status = (
            OrderStatus.FILLED if self.remaining_quantity <= 1e-12 else OrderStatus.PARTIALLY_FILLED
        )
        self.updated_at = fill.executed_at

    def reject(self, reason: RejectReason) -> None:
        """Mark the order as rejected with a reason."""
        self.status = OrderStatus.REJECTED
        self.reject_reason = reason
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "order_id": self.order_id,
            "request": self.request.to_dict(),
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "average_price": self.average_price,
            "reject_reason": self.reject_reason.value,
            "fills": [fill.to_dict() for fill in self.fills],
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }

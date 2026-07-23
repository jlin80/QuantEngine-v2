"""Modelo de posición: estado vivo de una operación abierta o cerrada.

La posición conoce su riesgo inicial (para medir en R), su stop/objetivo,
el trailing y el break-even. El PnL flotante se calcula contra un ``mark``
provisto por el mercado — la posición nunca consulta el mercado por sí misma.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.execution.models.enums import ExitReason, PositionSide, PositionStatus
from app.utils.time import utc_now


def _new_id() -> str:
    """Unique position identifier."""
    return uuid.uuid4().hex


def _iso(moment: datetime | None) -> str | None:
    """ISO-8601 or ``None`` passthrough."""
    return None if moment is None else moment.isoformat()


@dataclass(kw_only=True, slots=True)
class Position:
    """A single open (or closed) position.

    Attributes:
        position_id: Identificador de la posición.
        symbol: Símbolo.
        side: LONG o SHORT.
        quantity: Cantidad actual (se reduce con parciales).
        initial_quantity: Cantidad al abrir.
        entry_price: Precio medio de entrada.
        initial_stop: Stop inicial (base para medir el riesgo en R).
        stop_loss: Stop actual (puede moverse: BE/trailing).
        take_profit: Objetivo actual.
        opened_at: Apertura (UTC).
        closed_at: Cierre (UTC), si aplica.
        status: OPEN / CLOSED.
        realized_pnl: PnL realizado (parciales + cierre).
        commission_paid: Comisiones acumuladas.
        break_even_active: Si el stop ya está en break-even.
        trailing_active: Si el trailing stop está activo.
        mark_price: Último precio de valoración conocido.
        exit_reason: Motivo de cierre.
        decision_id: Decisión de origen.
        regime: Régimen de mercado al abrir.
        score: Score de la decisión de origen.
        confidence: Confianza de la decisión de origen.
        entry_reasons: Razones de entrada.
        exit_reasons: Razones de salida.
        metadata: Datos adicionales JSON-safe.
    """

    position_id: str = field(default_factory=_new_id)
    symbol: str
    side: PositionSide
    quantity: float
    initial_quantity: float
    entry_price: float
    initial_stop: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: datetime = field(default_factory=utc_now)
    closed_at: datetime | None = None
    status: PositionStatus = PositionStatus.OPEN
    realized_pnl: float = 0.0
    commission_paid: float = 0.0
    break_even_active: bool = False
    trailing_active: bool = False
    mark_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    exit_reason: ExitReason | None = None
    decision_id: str | None = None
    regime: str = "unknown"
    score: float = 0.0
    confidence: float = 0.0
    entry_reasons: tuple[str, ...] = ()
    exit_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Seed the mark and price extremes from the entry price."""
        if self.mark_price == 0.0:
            self.mark_price = self.entry_price
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price
        if self.lowest_price == 0.0:
            self.lowest_price = self.entry_price
        if self.stop_loss is not None and self.initial_stop is None:
            self.initial_stop = self.stop_loss

    @property
    def is_long(self) -> bool:
        """Whether the position is long."""
        return self.side is PositionSide.LONG

    @property
    def direction_sign(self) -> int:
        """+1 for long, -1 for short (used in PnL math)."""
        return 1 if self.is_long else -1

    @property
    def notional(self) -> float:
        """Nominal value at the mark price."""
        return self.quantity * self.mark_price

    @property
    def cost_basis(self) -> float:
        """Nominal value at the entry price."""
        return self.quantity * self.entry_price

    @property
    def risk_per_unit(self) -> float:
        """Distance from entry to the initial stop (0 if no stop)."""
        if self.initial_stop is None:
            return 0.0
        return abs(self.entry_price - self.initial_stop)

    @property
    def initial_risk(self) -> float:
        """Currency risked at open (initial_quantity × risk_per_unit)."""
        return self.risk_per_unit * self.initial_quantity

    def unrealized_pnl(self, mark: float | None = None) -> float:
        """Floating PnL at a mark price.

        Args:
            mark: Precio de valoración; usa ``mark_price`` si es ``None``.

        Returns:
            PnL flotante de la cantidad abierta.
        """
        price = self.mark_price if mark is None else mark
        return (price - self.entry_price) * self.quantity * self.direction_sign

    def r_multiple(self, mark: float | None = None) -> float:
        """Floating result expressed in R (0 if risk is undefined)."""
        risk = self.risk_per_unit
        if risk <= 0:
            return 0.0
        price = self.mark_price if mark is None else mark
        return (price - self.entry_price) * self.direction_sign / risk

    def update_mark(self, mark: float) -> None:
        """Update the mark price and the running price extremes."""
        self.mark_price = mark
        self.highest_price = max(self.highest_price, mark)
        self.lowest_price = min(self.lowest_price, mark)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "initial_quantity": self.initial_quantity,
            "entry_price": self.entry_price,
            "initial_stop": self.initial_stop,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "opened_at": _iso(self.opened_at),
            "closed_at": _iso(self.closed_at),
            "status": self.status.value,
            "mark_price": self.mark_price,
            "unrealized_pnl": self.unrealized_pnl(),
            "realized_pnl": self.realized_pnl,
            "r_multiple": self.r_multiple(),
            "notional": self.notional,
            "commission_paid": self.commission_paid,
            "break_even_active": self.break_even_active,
            "trailing_active": self.trailing_active,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
            "decision_id": self.decision_id,
            "regime": self.regime,
            "score": self.score,
            "confidence": self.confidence,
            "entry_reasons": list(self.entry_reasons),
            "exit_reasons": list(self.exit_reasons),
            "holding_seconds": self.holding_seconds(),
            "metadata": self.metadata,
        }

    def holding_seconds(self) -> float:
        """Seconds the position has been (or was) open."""
        end = self.closed_at or utc_now()
        return (end - self.opened_at).total_seconds()

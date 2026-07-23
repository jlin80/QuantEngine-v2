"""Enumeraciones de la capa de ejecución."""

import enum


class ExecutionMode(enum.StrEnum):
    """Modo de operación del motor de ejecución."""

    PAPER = "paper"
    DEMO = "demo"  # enruta órdenes reales a una cuenta *demo* (nunca dinero real)
    LIVE = "live"  # prohibido: dinero real, sigue duro-bloqueado por el Live Gate


class OrderSide(enum.StrEnum):
    """Lado de una orden."""

    BUY = "buy"
    SELL = "sell"


class OrderType(enum.StrEnum):
    """Tipos de orden soportados (algunos preparados para fases futuras)."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"


class TimeInForce(enum.StrEnum):
    """Vigencia de una orden."""

    GTC = "gtc"  # good-til-cancelled
    IOC = "ioc"  # immediate-or-cancel
    FOK = "fok"  # fill-or-kill
    DAY = "day"


class OrderStatus(enum.StrEnum):
    """Ciclo de vida de una orden."""

    CREATED = "created"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PositionSide(enum.StrEnum):
    """Dirección de una posición abierta."""

    LONG = "long"
    SHORT = "short"


class PositionStatus(enum.StrEnum):
    """Estado de una posición."""

    OPEN = "open"
    CLOSED = "closed"


class RejectReason(enum.StrEnum):
    """Motivos por los que una orden puede ser rechazada."""

    NONE = "none"
    RISK_BLOCKED = "risk_blocked"
    INSUFFICIENT_CAPITAL = "insufficient_capital"
    NO_MARKET_DATA = "no_market_data"
    SPREAD_TOO_WIDE = "spread_too_wide"
    INVALID_QUANTITY = "invalid_quantity"
    KILL_SWITCH = "kill_switch"
    CIRCUIT_BREAKER = "circuit_breaker"
    SIMULATED_REJECT = "simulated_reject"
    BROKER_REJECTED = "broker_rejected"  # el broker real rechazó la orden (retcode)
    BROKER_UNAVAILABLE = "broker_unavailable"  # sin conexión al broker


class ExitReason(enum.StrEnum):
    """Motivo por el que se cierra una posición."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    BREAK_EVEN = "break_even"
    TIME_EXIT = "time_exit"
    VOLATILITY_EXIT = "volatility_exit"
    REGIME_CHANGE = "regime_change"
    CONTEXT_LOST = "context_lost"
    RISK_EXIT = "risk_exit"
    KILL_SWITCH = "kill_switch"
    MANUAL = "manual"

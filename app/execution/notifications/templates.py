"""Constructores de embeds de Discord para eventos de ejecución.

Cada función traduce un evento de la capa de ejecución en un
:class:`~app.notifications.models.Notification` con título, nivel (color),
y campos (Activo, Dirección, Precio, SL, TP, PnL, R, Motivo, ID...). El canal
Discord ya renderiza el embed a partir de ese modelo — aquí sólo se decide el
contenido, nunca el transporte.
"""

from typing import Any

from app.execution.events import (
    BreakEvenActivated,
    CircuitBreakerTriggered,
    KillSwitchTriggered,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RiskTriggered,
    StopMoved,
    TrailingUpdated,
)
from app.notifications.models import Notification, NotificationLevel


def _money(value: float) -> str:
    """Format a currency amount with a sign."""
    return f"{value:+,.2f}"


def _price(value: float | None) -> str:
    """Format a price (or dash if absent)."""
    return "—" if value is None else f"{value:,.4f}"


def position_opened(event: PositionOpened) -> Notification:
    """Embed for a newly opened position."""
    arrow = "🟢 LONG" if event.side == "long" else "🔴 SHORT"
    return Notification(
        title=f"📈 Posición abierta · {event.symbol}",
        message=f"Nueva posición **{arrow}** en {event.symbol}.",
        level=NotificationLevel.SUCCESS,
        fields={
            "Activo": event.symbol,
            "Dirección": event.side.upper(),
            "Cantidad": f"{event.quantity:g}",
            "Entrada": _price(event.entry_price),
            "SL": _price(event.stop_loss),
            "TP": _price(event.take_profit),
            "ID": event.position_id[:8],
        },
        source="execution",
    )


def position_closed(event: PositionClosed) -> Notification:
    """Embed for a closed position (color by outcome)."""
    win = event.pnl > 0
    level = NotificationLevel.SUCCESS if win else NotificationLevel.WARNING
    emoji = "✅" if win else "🛑"
    minutes = event.holding_seconds / 60.0
    return Notification(
        title=f"{emoji} Posición cerrada · {event.symbol}",
        message=f"Cierre por **{event.exit_reason}** con PnL **{_money(event.pnl)}**.",
        level=level,
        fields={
            "Activo": event.symbol,
            "Dirección": event.side.upper(),
            "Cantidad": f"{event.quantity:g}",
            "Salida": _price(event.exit_price),
            "PnL": _money(event.pnl),
            "R": f"{event.r_multiple:+.2f}",
            "Motivo": event.exit_reason,
            "Tiempo": f"{minutes:.1f} min",
            "ID": event.position_id[:8],
        },
        source="execution",
    )


def stop_moved(event: StopMoved) -> Notification:
    """Embed for a stop-loss update."""
    return Notification(
        title=f"🔧 Stop movido · {event.symbol}",
        message=f"Stop actualizado por **{event.reason}**.",
        level=NotificationLevel.INFO,
        fields={
            "Activo": event.symbol,
            "Anterior": _price(event.previous),
            "Nuevo": _price(event.current),
            "Motivo": event.reason,
            "ID": event.position_id[:8],
        },
        source="execution",
    )


def break_even(event: BreakEvenActivated) -> Notification:
    """Embed for a break-even activation."""
    return Notification(
        title=f"🛡️ Break-even · {event.symbol}",
        message="Stop movido a break-even: operación sin riesgo.",
        level=NotificationLevel.SUCCESS,
        fields={"Activo": event.symbol, "Precio": _price(event.price), "ID": event.position_id[:8]},
        source="execution",
    )


def trailing_updated(event: TrailingUpdated) -> Notification:
    """Embed for a trailing-stop update."""
    return Notification(
        title=f"🏃 Trailing actualizado · {event.symbol}",
        message="Trailing stop ajustado a favor de la posición.",
        level=NotificationLevel.INFO,
        fields={
            "Activo": event.symbol,
            "Stop": _price(event.stop_loss),
            "ID": event.position_id[:8],
        },
        source="execution",
    )


def order_rejected(event: OrderRejected) -> Notification:
    """Embed for a rejected order."""
    return Notification(
        title=f"🚫 Orden rechazada · {event.symbol}",
        message=f"La orden fue rechazada: **{event.reason}**.",
        level=NotificationLevel.WARNING,
        fields={
            "Activo": event.symbol,
            "Dirección": event.side.upper(),
            "Motivo": event.reason,
        },
        source="execution",
    )


def risk_triggered(event: RiskTriggered) -> Notification:
    """Embed for a risk rule that blocked or forced an action."""
    return Notification(
        title=f"⚠️ Riesgo · {event.rule}",
        message=event.detail,
        level=NotificationLevel.WARNING,
        fields={"Regla": event.rule, "Activo": event.symbol or "—", "Detalle": event.detail},
        source="execution",
    )


def kill_switch(event: KillSwitchTriggered) -> Notification:
    """Embed for a kill-switch activation."""
    return Notification(
        title="🔴 KILL SWITCH ACTIVADO",
        message=f"No se abrirán posiciones nuevas: **{event.reason}**.",
        level=NotificationLevel.CRITICAL,
        fields={"Motivo": event.reason, "Drawdown": f"{event.drawdown_pct:.1f}%"},
        source="execution",
    )


def circuit_breaker(event: CircuitBreakerTriggered) -> Notification:
    """Embed for a circuit-breaker activation."""
    return Notification(
        title="⛔ CIRCUIT BREAKER",
        message=f"Operativa pausada: **{event.reason}**.",
        level=NotificationLevel.ERROR,
        fields={
            "Motivo": event.reason,
            "Pérdida": f"{event.loss_pct:.1f}%",
            "Ventana": f"{event.window_minutes:.0f} min",
        },
        source="execution",
    )


def report(title: str, snapshot: dict[str, Any], performance: dict[str, Any]) -> Notification:
    """Embed for a periodic performance report (hourly/daily/weekly)."""
    return Notification(
        title=title,
        message=(
            f"Equity **{snapshot.get('equity', 0):,.2f}** "
            f"({snapshot.get('return_pct', 0):+.2f}%) · "
            f"{performance.get('total_trades', 0)} operaciones."
        ),
        level=NotificationLevel.INFO,
        fields={
            "Equity": f"{snapshot.get('equity', 0):,.2f}",
            "Balance": f"{snapshot.get('balance', 0):,.2f}",
            "PnL flotante": _money(snapshot.get("floating_pnl", 0.0)),
            "Drawdown": f"{snapshot.get('drawdown_pct', 0):.2f}%",
            "Exposición": f"{snapshot.get('exposure_pct', 0):.1f}%",
            "Posiciones": str(snapshot.get("open_positions", 0)),
            "Win rate": f"{performance.get('win_rate', 0) * 100:.1f}%",
            "Profit factor": f"{performance.get('profit_factor', 0):.2f}",
            "Expectativa": _money(performance.get("expectancy", 0.0)),
            "Operaciones": str(performance.get("total_trades", 0)),
        },
        source="execution",
    )

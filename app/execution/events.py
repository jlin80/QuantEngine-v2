"""Eventos de la capa de ejecución (solo campos primitivos, JSON-safe).

Se publican en el Event Bus para que Notificaciones, Dashboard y Watchdog
reaccionen sin acoplarse a la implementación del Execution Engine.
"""

from dataclasses import dataclass

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderCreated(Event):
    """Se creó una orden a partir de una decisión aceptada."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    decision_id: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderRejected(Event):
    """Una orden fue rechazada (riesgo, capital, datos, rechazo simulado)."""

    order_id: str
    symbol: str
    side: str
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderExecuted(Event):
    """Una orden se ejecutó (total o parcialmente) en el Paper Engine."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    slippage_bps: float
    partial: bool = False


@dataclass(frozen=True, kw_only=True, slots=True)
class PositionOpened(Event):
    """Se abrió una posición nueva."""

    position_id: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    decision_id: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class PositionClosed(Event):
    """Se cerró una posición."""

    position_id: str
    symbol: str
    side: str
    quantity: float
    exit_price: float
    pnl: float
    r_multiple: float
    exit_reason: str
    holding_seconds: float


@dataclass(frozen=True, kw_only=True, slots=True)
class PositionModified(Event):
    """Una parcial redujo la cantidad de una posición (estructura preparada)."""

    position_id: str
    symbol: str
    quantity: float
    closed_quantity: float
    realized_pnl: float


@dataclass(frozen=True, kw_only=True, slots=True)
class StopMoved(Event):
    """El stop de una posición se movió."""

    position_id: str
    symbol: str
    previous: float | None
    current: float
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class BreakEvenActivated(Event):
    """El stop de una posición se movió a break-even."""

    position_id: str
    symbol: str
    price: float


@dataclass(frozen=True, kw_only=True, slots=True)
class TrailingUpdated(Event):
    """El trailing stop de una posición se actualizó."""

    position_id: str
    symbol: str
    stop_loss: float


@dataclass(frozen=True, kw_only=True, slots=True)
class RiskTriggered(Event):
    """Un límite de riesgo bloqueó o forzó una acción."""

    rule: str
    symbol: str
    detail: str


@dataclass(frozen=True, kw_only=True, slots=True)
class KillSwitchTriggered(Event):
    """El kill switch se activó: no se abren posiciones nuevas."""

    reason: str
    drawdown_pct: float


@dataclass(frozen=True, kw_only=True, slots=True)
class CircuitBreakerTriggered(Event):
    """El cortacircuitos se activó por pérdida rápida en una ventana."""

    reason: str
    loss_pct: float
    window_minutes: float


@dataclass(frozen=True, kw_only=True, slots=True)
class DiscordNotificationSent(Event):
    """Una notificación de ejecución se entregó a Discord."""

    title: str
    level: str


@dataclass(frozen=True, kw_only=True, slots=True)
class DiscordNotificationFailed(Event):
    """La entrega de una notificación de ejecución falló."""

    title: str
    error: str


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyExperimentOpened(Event):
    """Se abrió un experimento con fecha de corte sobre una estrategia."""

    strategy: str
    deadline: str  # ISO-8601 UTC
    min_trades: int
    max_expectancy_r: float


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyExperimentVerdict(Event):
    """Venció un experimento y se emitió su veredicto.

    ``outcome`` es ``deactivation_candidate`` / ``passed`` / ``extended``.
    Un veredicto **nunca** desactiva la estrategia: sólo informa. Apagarla es
    mover ``execution.strategies_enabled`` a mano.
    """

    strategy: str
    outcome: str
    trades: int
    expectancy_r: float
    win_rate: float
    total_r: float
    window_hours: float
    detail: str


@dataclass(frozen=True, kw_only=True, slots=True)
class HoldingChangeFalsified(Event):
    """Veredicto de la falsación del cambio de holding por estrategia.

    ``outcome`` es ``confirmed`` / ``refuted`` / ``pending``. Se publica
    **acierte o falle**: una predicción que sólo se reporta cuando se cumple no
    es una falsación, es una felicitación.
    """

    outcome: str
    trades: int
    window_hours: float
    take_profit_pct: float
    regime_change_pct: float
    median_holding_seconds: float
    expected_holding_seconds: float
    detail: str

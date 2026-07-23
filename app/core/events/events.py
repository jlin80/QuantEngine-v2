"""Eventos de dominio y de sistema.

Fase 1: se definen los contratos (payloads tipados) aunque los módulos que
los publican (estrategias, ejecución, brokers) aún no existan. Esto fija el
vocabulario del sistema desde el inicio.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.events.base import Event

# ---------------------------------------------------------------------------
# Eventos de mercado
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class PriceUpdated(Event):
    """New market price for an instrument."""

    symbol: str
    bid: float
    ask: float
    timestamp: datetime


# ---------------------------------------------------------------------------
# Eventos de trading (contratos para fases futuras)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalCreated(Event):
    """A strategy produced a trading signal."""

    strategy: str
    symbol: str
    direction: str  # "long" | "short"
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, slots=True)
class TradeOpened(Event):
    """A trade was opened (paper or live)."""

    trade_id: str
    symbol: str
    direction: str
    volume: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    mode: str = "paper"  # "paper" | "live"


@dataclass(frozen=True, kw_only=True, slots=True)
class TradeClosed(Event):
    """A trade was closed with its final result."""

    trade_id: str
    symbol: str
    exit_price: float
    pnl: float
    reason: str = "unknown"
    mode: str = "paper"


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderFilled(Event):
    """Broker confirmed an order fill."""

    order_id: str
    symbol: str
    volume: float
    fill_price: float


@dataclass(frozen=True, kw_only=True, slots=True)
class PositionClosed(Event):
    """A position was fully closed at the broker."""

    position_id: str
    symbol: str
    pnl: float


@dataclass(frozen=True, kw_only=True, slots=True)
class PaperTradeExecuted(Event):
    """The paper-trading simulator executed a fill."""

    trade_id: str
    symbol: str
    direction: str
    volume: float
    price: float


@dataclass(frozen=True, kw_only=True, slots=True)
class RiskTriggered(Event):
    """A risk rule fired (drawdown limit, exposure limit, blackout...)."""

    rule: str
    severity: str  # "warning" | "critical"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyLoaded(Event):
    """A strategy module was loaded and registered."""

    strategy: str
    symbols: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Eventos de sistema
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class SystemStarted(Event):
    """The engine finished its startup sequence."""

    environment: str
    version: str


@dataclass(frozen=True, kw_only=True, slots=True)
class SystemStopping(Event):
    """The engine is beginning a graceful shutdown."""

    reason: str = "requested"


@dataclass(frozen=True, kw_only=True, slots=True)
class ConnectionLost(Event):
    """Connectivity to an external dependency was lost."""

    target: str  # p.ej. "redis", "postgres", "broker"
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class ConnectionRestored(Event):
    """Connectivity to an external dependency was restored."""

    target: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ModuleFrozen(Event):
    """Watchdog detected a component that stopped heartbeating."""

    module: str
    seconds_since_heartbeat: float
    action: str = "none"  # "none" | "restarted" | "restart_failed" | "exhausted"


@dataclass(frozen=True, kw_only=True, slots=True)
class ModuleHealthChanged(Event):
    """Overall or per-module health status transitioned."""

    module: str
    previous: str
    current: str
    detail: str = ""

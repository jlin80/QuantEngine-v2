"""Sistema de eventos: contratos base, eventos de dominio y Event Bus."""

from app.core.events.base import Event
from app.core.events.bus import EventBus, Subscription
from app.core.events.events import (
    ConnectionLost,
    ConnectionRestored,
    ModuleFrozen,
    ModuleHealthChanged,
    OrderFilled,
    PaperTradeExecuted,
    PositionClosed,
    PriceUpdated,
    RiskTriggered,
    SignalCreated,
    StrategyLoaded,
    SystemStarted,
    SystemStopping,
    TradeClosed,
    TradeOpened,
)

__all__ = [
    "ConnectionLost",
    "ConnectionRestored",
    "Event",
    "EventBus",
    "ModuleFrozen",
    "ModuleHealthChanged",
    "OrderFilled",
    "PaperTradeExecuted",
    "PositionClosed",
    "PriceUpdated",
    "RiskTriggered",
    "SignalCreated",
    "StrategyLoaded",
    "Subscription",
    "SystemStarted",
    "SystemStopping",
    "TradeClosed",
    "TradeOpened",
]

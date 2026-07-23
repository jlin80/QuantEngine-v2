"""Fábrica del stack de ejecución para el backtest (Fase 6).

Ensambla exactamente la misma pila que ``bootstrap._build_execution`` (Fase 5)
—comisiones, slippage, latencia, paper broker, sizing, riesgo, cartera,
posiciones, journal, performance y el Execution Engine— pero desacoplada del
bus y del contenedor DI, con el journal en memoria (sin persistir al archivo de
producción). El backtest **reutiliza el motor real**, no un clon.
"""

import random
from dataclasses import dataclass

from app.config.settings import ExecutionSettings
from app.execution.commission import CommissionEngine
from app.execution.execution_engine import ExecutionEngine
from app.execution.journal import TradeJournal
from app.execution.latency import LatencyEngine
from app.execution.order_manager import OrderManager
from app.execution.paper_engine import PaperBroker
from app.execution.performance import PerformanceEngine
from app.execution.portfolio_manager import PortfolioManager
from app.execution.position_manager import PositionManager
from app.execution.risk_manager import RiskManager
from app.execution.sizing import PositionSizer
from app.execution.slippage import SlippageEngine
from app.market.services import MarketDataService


@dataclass(frozen=True, slots=True)
class ExecutionStack:
    """Bundle of the wired execution components used by a backtest."""

    engine: ExecutionEngine
    positions: PositionManager
    portfolio: PortfolioManager
    risk: RiskManager
    journal: TradeJournal
    performance: PerformanceEngine
    paper: PaperBroker


def build_execution_stack(
    settings: ExecutionSettings,
    market: MarketDataService,
    *,
    rng: random.Random | None = None,
) -> ExecutionStack:
    """Wire a self-contained execution stack for a backtest run.

    Args:
        settings: Configuración de ejecución (misma que en paper real).
        market: Servicio de datos histórico que alimenta al motor.
        rng: Fuente aleatoria del Paper Engine (determinismo en tests).

    Returns:
        Un :class:`ExecutionStack` listo para recibir decisiones, sin bus ni
        contexto (el backtest los suple con datos históricos y su reloj).
    """
    commission = CommissionEngine(settings.commission)
    slippage = SlippageEngine(settings.slippage)
    latency = LatencyEngine(settings.latency)
    paper = PaperBroker(settings, commission, slippage, latency, rng=rng)

    sizer = PositionSizer(settings.sizing)
    positions = PositionManager(
        break_even_r=settings.break_even_r,
        trailing_enabled=settings.trailing_enabled,
        trailing_atr_multiple=settings.trailing_atr_multiple,
    )
    portfolio = PortfolioManager(
        settings.initial_balance,
        base_currency=settings.base_currency,
        leverage=settings.leverage,
    )
    risk = RiskManager(settings.risk, settings.initial_balance)
    orders = OrderManager()
    journal = TradeJournal(None, persist=False)
    performance = PerformanceEngine(settings.initial_balance)

    engine = ExecutionEngine(
        settings,
        market,
        paper,
        commission,
        sizer,
        risk,
        positions,
        portfolio,
        orders,
        journal,
        performance,
        bus=None,
        context=None,
    )
    return ExecutionStack(
        engine=engine,
        positions=positions,
        portfolio=portfolio,
        risk=risk,
        journal=journal,
        performance=performance,
        paper=paper,
    )

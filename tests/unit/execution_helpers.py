"""Constructores compartidos para las pruebas de la capa de ejecución (Fase 5).

Todo con componentes reales: un ``ExecutionEngine`` sobre un
``MarketDataService`` poblado a mano y un RNG sembrado para determinismo.
"""

import random
from collections.abc import Callable

from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.config.settings import ExecutionSettings
from app.execution.commission import CommissionEngine
from app.execution.execution_engine import ExecutionEngine
from app.execution.falsification import HoldingChangeFalsifier
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
from app.execution.strategy_experiments import StrategyExperimentManager
from app.market.cache import MarketCache
from app.market.models import Candle, Ticker
from app.market.services import MarketDataService, MarketStateStore


def make_market_with_state(
    candles: list[Candle] | None = None, ticker: Ticker | None = None
) -> tuple[MarketDataService, MarketStateStore]:
    """MarketDataService y su estado subyacente (para mutar el ticker en tests)."""
    state = MarketStateStore()
    for candle in candles or []:
        state.update_candle(candle)
    if ticker is not None:
        state.update_ticker(ticker)
    cache = MarketCache(CacheService(primary=None, fallback=InMemoryCache()))
    return MarketDataService(state, cache, None), state


def make_execution_settings(**overrides: object) -> ExecutionSettings:
    """Execution settings sin persistencia a disco (paper puro)."""
    data: dict[str, object] = {"enabled": True, "persist_journal": False}
    data.update(overrides)
    return ExecutionSettings(**data)  # type: ignore[arg-type]


def make_engine(
    market: MarketDataService,
    settings: ExecutionSettings | None = None,
    *,
    seed: int = 7,
    atr_pct_high_for: Callable[[str], float] | None = None,
) -> ExecutionEngine:
    """Construye un ExecutionEngine determinista sobre un mercado dado."""
    cfg = settings or make_execution_settings()
    rng = random.Random(seed)
    commission = CommissionEngine(cfg.commission)
    paper = PaperBroker(
        cfg,
        commission,
        SlippageEngine(cfg.slippage, rng),
        LatencyEngine(cfg.latency, rng),
        rng,
    )
    return ExecutionEngine(
        cfg,
        market,
        paper,
        commission,
        PositionSizer(cfg.sizing),
        RiskManager(cfg.risk, cfg.initial_balance),
        PositionManager(
            break_even_r=cfg.break_even_r,
            trailing_enabled=cfg.trailing_enabled,
            trailing_atr_multiple=cfg.trailing_atr_multiple,
        ),
        PortfolioManager(cfg.initial_balance, leverage=cfg.leverage),
        OrderManager(),
        # Respeta la configuración (como hace el composition root): por defecto
        # `persist_journal=False`, pero un test puede pedir persistencia real.
        TradeJournal(
            cfg.journal_path if cfg.persist_journal else None,
            persist=cfg.persist_journal,
        ),
        PerformanceEngine(cfg.initial_balance),
        None,
        None,
        # Igual que el composition root: el motor siempre lleva su gestor
        # de experimentos, que sólo propone desactivaciones.
        StrategyExperimentManager(cfg.experiments),
        HoldingChangeFalsifier(cfg.falsification, cfg),
        risk_multiplier_reader=None,
        atr_pct_high_for=atr_pct_high_for,
    )

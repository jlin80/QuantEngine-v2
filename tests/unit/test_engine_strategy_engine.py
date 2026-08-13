"""Strategy Engine: plugins, cadencias, concurrencia y aislamiento."""

import asyncio
from pathlib import Path

import pytest
from app.config.settings import (
    QuantConfidenceSettings,
    QuantConsensusSettings,
    QuantContextSettings,
    QuantRegimeSettings,
    QuantSettings,
    QuantStrategySettings,
)
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.exceptions import ConfigurationError
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.feature_store import FeatureStore
from app.engine.filters import FilterChain
from app.engine.market_context import MarketContextEngine
from app.engine.plugins import PluginLoader
from app.engine.regime_detection import RegimeDetector
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import SignalHistoryStore
from app.engine.strategy_engine import StrategyEngine
from app.engine.validators import SignalValidator
from app.market.events import CandleClosed, NewTick
from app.scheduler.scheduler import AsyncScheduler

from tests.unit.quant_helpers import make_candles, make_market, make_ticker, make_trade

ALWAYS_LONG = """
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, Direction, StrategySignal
from app.market.models import Timeframe


class AlwaysLong(BaseStrategy):
    name = "always_long"
    version = "1.0"
    symbols = ("BTCUSDT",)
    cadence = Cadence(kind=CadenceKind.EVERY_CANDLE, timeframe=Timeframe.M1)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            timestamp=ctx.fired_at,
            direction=Direction.LONG,
            confidence=0.8,
            score=80.0,
            reasons=("siempre alcista",),
        )
"""

BROKEN = """
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, StrategySignal
from app.market.models import Timeframe


class Broken(BaseStrategy):
    name = "broken"
    symbols = ("BTCUSDT",)
    cadence = Cadence(kind=CadenceKind.EVERY_CANDLE, timeframe=Timeframe.M1)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        raise RuntimeError("bug interno de la estrategia")
"""

SLOW = """
import asyncio

from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, StrategySignal
from app.market.models import Timeframe


class Slow(BaseStrategy):
    name = "slow"
    symbols = ("BTCUSDT",)
    cadence = Cadence(kind=CadenceKind.EVERY_CANDLE, timeframe=Timeframe.M1)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        await asyncio.sleep(0.15)
        return None
"""

TICKER_STRAT = """
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, StrategySignal


class OnTick(BaseStrategy):
    name = "on_tick"
    symbols = ("BTCUSDT",)
    cadence = Cadence(kind=CadenceKind.EVERY_TICK)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        return None
"""

TIMER_STRAT = """
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, StrategySignal


class OnTimer(BaseStrategy):
    name = "on_timer"
    symbols = ("BTCUSDT",)
    cadence = Cadence(kind=CadenceKind.INTERVAL, seconds=0.5)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        return None
"""


class Stack:
    """Pila completa del Quant Core sobre mercado sintético."""

    def __init__(
        self,
        plugin_dir: Path,
        overrides: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.bus = EventBus()
        self.scheduler = AsyncScheduler()
        self.market = make_market(
            candles=make_candles([100.0, 101.0] * 25),
            ticker=make_ticker(),
            trades=[make_trade(price=100.5)],
        )
        self.settings = QuantSettings(
            enabled=True,
            plugin_dirs=[plugin_dir],
            auto_discover=True,
            consensus=QuantConsensusSettings(
                min_signals=1, min_score=0.0, min_confidence=0.0, min_agreement=0.0
            ),
            strategies={"always_long": QuantStrategySettings(weight=2.0)},
        )
        self.history = SignalHistoryStore()
        self.signals = SignalEngine(SignalValidator(), self.history, self.bus)
        features = FeatureStore(self.market)
        regime = RegimeDetector(self.market, QuantRegimeSettings())
        context = MarketContextEngine(self.market, features, regime, QuantContextSettings())
        consensus = ConsensusEngine(self.settings.consensus)
        confidence = ConfidenceEngine(QuantConfidenceSettings())
        self.decisions = DecisionEngine(
            self.signals,
            context,
            consensus,
            confidence,
            FilterChain([]),
            self.history,
            self.settings.consensus,
            self.bus,
        )
        self.engine = StrategyEngine(
            self.settings,
            PluginLoader([plugin_dir]),
            self.market,
            features,
            context,
            self.signals,
            self.decisions,
            self.bus,
            self.scheduler,
            overrides=(lambda: overrides) if overrides is not None else None,
        )
        self.events: list[str] = []

    async def start(self) -> None:
        await self.bus.start()

        async def collector(event: Event) -> None:
            self.events.append(event.name)

        self.bus.subscribe(collector)
        await self.scheduler.start()
        await self.engine.start()

    async def stop(self) -> None:
        await self.engine.stop()
        await self.scheduler.stop()
        await self.bus.stop()


def _candle_event() -> CandleClosed:
    return CandleClosed(
        source="test",
        symbol="BTCUSDT",
        provider="test",
        timeframe="1m",
        start="2026-07-15T12:00:00+00:00",
        end="2026-07-15T12:01:00+00:00",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        vwap=100.2,
        trades=5,
    )


async def _stack(
    tmp_path: Path,
    plugins: dict[str, str],
    overrides: dict[str, dict[str, object]] | None = None,
) -> Stack:
    for name, source in plugins.items():
        (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    stack = Stack(tmp_path, overrides)
    await stack.start()
    return stack


async def test_auto_discovery_loads_and_announces(tmp_path: Path):
    stack = await _stack(tmp_path, {"always_long": ALWAYS_LONG, "on_tick": TICKER_STRAT})
    try:
        await asyncio.sleep(0.05)
        assert stack.engine.loaded == ["always_long", "on_tick"]
        assert stack.engine.weights()["always_long"] == 2.0
        assert stack.events.count("StrategyLoaded") == 2
    finally:
        await stack.stop()


async def test_candle_close_runs_strategy_through_to_decision(tmp_path: Path):
    stack = await _stack(tmp_path, {"always_long": ALWAYS_LONG})
    try:
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.25)

        stats = stack.engine.stats()[0]
        assert stats.runs == 1
        assert stats.signals_produced == 1
        assert stats.last_duration_ms is not None
        decisions = stack.history.decisions()
        assert decisions and decisions[-1].accepted is True
        assert "StrategyExecuted" in stack.events
        assert "DecisionGenerated" in stack.events
    finally:
        await stack.stop()


async def test_broken_strategy_is_isolated(tmp_path: Path):
    stack = await _stack(tmp_path, {"always_long": ALWAYS_LONG, "broken": BROKEN})
    try:
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.25)

        by_name = {stats.name: stats for stats in stack.engine.stats()}
        assert by_name["broken"].errors == 1
        assert by_name["always_long"].signals_produced == 1, "la sana no se ve afectada"
        assert "StrategyFailed" in stack.events
    finally:
        await stack.stop()


async def test_busy_strategy_skips_overlapping_trigger(tmp_path: Path):
    stack = await _stack(tmp_path, {"slow": SLOW})
    try:
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.05)  # la primera evaluación toma el lock
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.3)

        stats = stack.engine.stats()[0]
        assert stats.runs == 1
        assert stats.skipped == 1
    finally:
        await stack.stop()


async def test_tick_cadence_dispatch(tmp_path: Path):
    stack = await _stack(tmp_path, {"on_tick": TICKER_STRAT})
    try:
        tick = NewTick(
            source="test",
            symbol="BTCUSDT",
            provider="test",
            price=100.0,
            size=1.0,
            side="buy",
            exchange_ts="2026-07-15T12:00:00+00:00",
            latency_ms=5.0,
        )
        await stack.engine._on_tick(tick)
        await asyncio.sleep(0.15)
        assert stack.engine.stats()[0].runs == 1
    finally:
        await stack.stop()


async def test_timer_cadence_runs_periodically(tmp_path: Path):
    stack = await _stack(tmp_path, {"on_timer": TIMER_STRAT})
    try:
        await asyncio.sleep(0.8)
        assert stack.engine.stats()[0].runs >= 1
    finally:
        await stack.stop()


async def test_enable_disable_unload_and_explain(tmp_path: Path):
    stack = await _stack(tmp_path, {"always_long": ALWAYS_LONG})
    try:
        stack.engine.disable_strategy("always_long")
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.15)
        assert stack.engine.stats()[0].runs == 0, "deshabilitada no se ejecuta"

        stack.engine.enable_strategy("always_long")
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.25)
        assert stack.engine.stats()[0].runs == 1
        assert "always_long" in stack.engine.explain("always_long")

        await stack.engine.unload_strategy("always_long")
        assert stack.engine.loaded == []
        with pytest.raises(ConfigurationError):
            stack.engine.disable_strategy("always_long")
    finally:
        await stack.stop()


async def test_status_snapshot_for_dashboard(tmp_path: Path):
    stack = await _stack(tmp_path, {"always_long": ALWAYS_LONG})
    try:
        status = stack.engine.status()
        assert status["loaded"] == 1
        assert status["enabled"] == 1
        assert status["strategies"][0]["name"] == "always_long"
        assert "feature_store" in status
    finally:
        await stack.stop()


# --------------------------------------------------------------------------
# Overrides del operador. Apagar una estrategia desde el dashboard sólo mutaba
# el motor en memoria: el siguiente reinicio la volvía a levantar con el valor
# del `.env`, sin avisar, y la decisión del operador se perdía en silencio.
# --------------------------------------------------------------------------


async def test_operator_override_survives_restart(tmp_path: Path):
    """Un `disable` persistido se reaplica tras el descubrimiento."""
    stack = await _stack(
        tmp_path,
        {"always_long": ALWAYS_LONG},
        overrides={"always_long": {"enabled": False}},
    )
    try:
        assert stack.engine.stats()[0].enabled is False
        await stack.engine._on_candle(_candle_event())
        await asyncio.sleep(0.15)
        assert stack.engine.stats()[0].runs == 0, "el override debe impedir la evaluación"
    finally:
        await stack.stop()


async def test_operator_weight_override_applies(tmp_path: Path):
    stack = await _stack(
        tmp_path,
        {"always_long": ALWAYS_LONG},
        overrides={"always_long": {"weight": 0.25}},
    )
    try:
        assert stack.engine.weights()["always_long"] == 0.25
        assert stack.engine.stats()[0].weight == 0.25
    finally:
        await stack.stop()


async def test_override_for_unknown_strategy_does_not_break_startup(tmp_path: Path):
    """Una estrategia retirada del catálogo no puede impedir el arranque."""
    stack = await _stack(
        tmp_path,
        {"always_long": ALWAYS_LONG},
        overrides={"ya_no_existe": {"enabled": False}, "always_long": {"weight": 3.0}},
    )
    try:
        assert stack.engine.loaded == ["always_long"]
        assert stack.engine.weights()["always_long"] == 3.0
    finally:
        await stack.stop()

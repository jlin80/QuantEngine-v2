"""Integración: la biblioteca real se descubre como plugins y llega a la API."""

from pathlib import Path

import pytest
from app.config.settings import QuantSettings, Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.evaluation import PerformanceTracker
from app.engine.feature_store import FeatureStore
from app.engine.filters import FilterChain
from app.engine.market_context import MarketContextEngine
from app.engine.plugins import PluginLoader
from app.engine.quant_core import QuantCore
from app.engine.regime_detection import RegimeDetector
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import SignalHistoryStore
from app.engine.strategy_engine import StrategyEngine
from app.engine.validators import SignalValidator
from app.scheduler.scheduler import AsyncScheduler
from fastapi.testclient import TestClient

from tests.unit.quant_helpers import make_candles, make_market, make_trade

pytestmark = pytest.mark.integration

EXPECTED_STRATEGIES = {
    "vwap_mean_reversion",
    "vwap_breakout",
    "anchored_vwap",
    "liquidity_sweep",
    "order_block",
    "fair_value_gap",
    "bos",
    "choch",
    "mss",
    "delta_confirmation",
    "cvd",
    "orderbook_imbalance",
    "volume_profile",
    "opening_range_breakout",
    "momentum_continuation",
    "atr_expansion",
    "volatility_compression",
    "trend_pullback",
    "mean_reversion",
    "range_breakout",
}


def test_plugin_loader_discovers_the_whole_library():
    """Los directorios por defecto contienen exactamente las 20 estrategias."""
    quant = QuantSettings()
    loader = PluginLoader(list(quant.plugin_dirs))
    classes = loader.discover()
    assert {cls.name for cls in classes} == EXPECTED_STRATEGIES
    # Todas instancian con sus parámetros por defecto (sin constantes rotas).
    for cls in classes:
        instance = cls()
        assert instance.parameters["lookback"] > 0
        assert "score_weights" in instance.parameters


def test_plugin_dirs_cover_the_eight_categories():
    quant = QuantSettings()
    names = {Path(directory).name for directory in quant.plugin_dirs}
    assert names == {
        "trend",
        "momentum",
        "orderflow",
        "smc",
        "volume",
        "volatility",
        "mean_reversion",
        "breakout",
    }


@pytest.fixture()
def client_with_performance(settings: Settings, tmp_path: Path):
    quant = settings.quant
    market = make_market(
        candles=make_candles([100.0, 101.0] * 25), trades=[make_trade(price=100.5)]
    )
    features = FeatureStore(market)
    regime = RegimeDetector(market, quant.regime)
    context = MarketContextEngine(market, features, regime, quant.context)
    history = SignalHistoryStore()
    signals = SignalEngine(SignalValidator(), history, None)
    consensus = ConsensusEngine(quant.consensus)
    confidence = ConfidenceEngine(quant.confidence)
    decisions = DecisionEngine(
        signals, context, consensus, confidence, FilterChain([]), history, quant.consensus, None
    )
    loader = PluginLoader([tmp_path])
    strategies = StrategyEngine(
        quant, loader, market, features, context, signals, decisions, EventBus(), AsyncScheduler()
    )
    evaluation = quant.evaluation.model_copy(update={"snapshot_path": tmp_path / "stats.json"})
    tracker = PerformanceTracker(evaluation, market)
    core = QuantCore(
        strategies=strategies,
        signals=signals,
        decisions=decisions,
        consensus=consensus,
        confidence=confidence,
        context=context,
        regime=regime,
        filters=FilterChain([]),
        features=features,
        history=history,
        loader=loader,
        performance=tracker,
    )
    container = Container()
    container.register_instance(QuantCore, core)
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_performance_endpoint(client_with_performance: TestClient):
    response = client_with_performance.get("/api/engine/performance")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["open_virtual_trades"] == 0
    assert body["strategies"] == {}


def test_engine_status_includes_performance(client_with_performance: TestClient):
    body = client_with_performance.get("/api/engine/status").json()
    assert body["performance"] is not None
    assert "open_virtual_trades" in body["performance"]


def test_strategy_detail_404_when_not_loaded(client_with_performance: TestClient):
    response = client_with_performance.get("/api/engine/strategies/ghost")
    assert response.status_code == 404

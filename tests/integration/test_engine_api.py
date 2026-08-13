"""Integración: endpoints /api/engine/* con un Quant Core real cableado."""

import asyncio
from pathlib import Path

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.feature_store import FeatureStore
from app.engine.filters import build_filter_chain
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


def _build_core(settings: Settings, plugin_dir: Path) -> QuantCore:
    quant = settings.quant
    bus = EventBus()
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
    filters = build_filter_chain(
        quant.filters,
        drawdown_reader=lambda: history.get_state("daily_drawdown_pct"),
        recent_decisions=lambda: history.decisions(limit=50),
    )
    decisions = DecisionEngine(
        signals, context, consensus, confidence, filters, history, quant.consensus, None
    )
    loader = PluginLoader([plugin_dir])
    strategies = StrategyEngine(
        quant, loader, market, features, context, signals, decisions, bus, AsyncScheduler()
    )
    return QuantCore(
        strategies=strategies,
        signals=signals,
        decisions=decisions,
        consensus=consensus,
        confidence=confidence,
        context=context,
        regime=regime,
        filters=filters,
        features=features,
        history=history,
        loader=loader,
    )


@pytest.fixture()
def client(settings: Settings, tmp_path: Path):
    container = Container()
    container.register_instance(QuantCore, _build_core(settings, tmp_path))
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_engine_status(client: TestClient):
    response = client.get("/api/engine/status")
    assert response.status_code == 200
    body = response.json()
    for key in ("strategies", "signals", "consensus", "filters", "history"):
        assert key in body


def test_engine_strategies_empty(client: TestClient):
    response = client.get("/api/engine/strategies")
    assert response.status_code == 200
    assert response.json() == {"strategies": [], "explanations": {}}


def test_engine_signals_and_status_filter(client: TestClient):
    assert client.get("/api/engine/signals").json() == {"signals": []}
    assert client.get("/api/engine/signals", params={"status": "rejected"}).status_code == 200
    assert client.get("/api/engine/signals", params={"status": "bogus"}).status_code == 422


def test_engine_decisions_empty(client: TestClient):
    assert client.get("/api/engine/decisions").json() == {"decisions": []}


def test_engine_regime_endpoint(client: TestClient):
    response = client.get("/api/engine/regime/BTCUSDT")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["primary"] in (
        "trending",
        "ranging",
        "expansion",
        "compression",
        "breakout",
        "reversal",
        "high_volatility",
        "low_volatility",
        "unknown",
    )


def test_engine_context_endpoint(client: TestClient):
    response = client.get("/api/engine/context/BTCUSDT")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert "regime" in body and "volatility" in body and "data_quality" in body


def test_engine_filters_endpoint(client: TestClient):
    response = client.get("/api/engine/filters/BTCUSDT")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert len(body["filters"]) > 0
    assert all("name" in f and "passed" in f for f in body["filters"])


def test_engine_consensus_endpoint(client: TestClient):
    response = client.get("/api/engine/consensus")
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "weighted_average"
    assert len(body["available"]) == 5


def test_engine_endpoints_503_without_core(settings: Settings):
    app = create_app(settings, Container())
    with TestClient(app) as client:
        assert client.get("/api/engine/status").status_code == 503


# --------------------------------------------------------------------------
# Control de estrategias. Estos endpoints sólo escribían la intención en el
# almacén de configuración, que no leía nadie: el dashboard confirmaba
# "Strategy disabled" y la estrategia seguía evaluando indefinidamente.
# --------------------------------------------------------------------------

ALWAYS_LONG = """
from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Cadence, CadenceKind, Direction, StrategySignal
from app.market.models import Timeframe


class AlwaysLong(BaseStrategy):
    name = "always_long"
    symbols = ("BTCUSDT",)
    cadence = Cadence(kind=CadenceKind.EVERY_CANDLE, timeframe=Timeframe.M1)

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        return None
"""


@pytest.fixture()
def loaded_client(settings: Settings, tmp_path: Path):
    """Cliente con una estrategia realmente cargada en el motor."""
    (tmp_path / "always_long.py").write_text(ALWAYS_LONG, encoding="utf-8")
    core = _build_core(settings, tmp_path)
    asyncio.run(core.strategies.start())
    container = Container()
    container.register_instance(QuantCore, core)
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_disable_strategy_actually_disables_it(loaded_client: TestClient):
    assert loaded_client.get("/api/engine/strategies").json()["strategies"][0]["enabled"] is True

    response = loaded_client.post("/api/engine/strategies/always_long/disable")
    assert response.status_code == 200
    assert response.json()["applied"]["enabled"] is False

    # Lo que importa: la lectura que pinta la tabla refleja el cambio.
    listed = loaded_client.get("/api/engine/strategies").json()["strategies"][0]
    assert listed["enabled"] is False

    assert loaded_client.post("/api/engine/strategies/always_long/enable").status_code == 200
    assert loaded_client.get("/api/engine/strategies").json()["strategies"][0]["enabled"] is True


def test_set_weight_applies_and_is_reflected(loaded_client: TestClient):
    response = loaded_client.patch(
        "/api/engine/strategies/always_long/weight", json={"weight": 0.4}
    )
    assert response.status_code == 200
    assert response.json()["applied"]["weight"] == 0.4
    assert loaded_client.get("/api/engine/strategies").json()["strategies"][0]["weight"] == 0.4


def test_unknown_strategy_is_404_not_a_silent_ok(loaded_client: TestClient):
    """Antes devolvía 200 para cualquier nombre, incluso inventado."""
    assert loaded_client.post("/api/engine/strategies/no_existe/disable").status_code == 404
    assert loaded_client.post("/api/engine/strategies/no_existe/enable").status_code == 404
    assert (
        loaded_client.patch(
            "/api/engine/strategies/no_existe/weight", json={"weight": 1.0}
        ).status_code
        == 404
    )


def test_negative_weight_is_rejected(loaded_client: TestClient):
    response = loaded_client.patch(
        "/api/engine/strategies/always_long/weight", json={"weight": -1.0}
    )
    assert response.status_code == 422


def test_backtest_cancel_no_longer_lies(client: TestClient):
    """Devolvía ``{"status": "cancelled"}`` sin cancelar nada."""
    response = client.post("/api/backtesting/cancel/whatever")
    assert response.status_code == 409
    assert "sincrona" in response.json()["detail"].lower().replace("í", "i")

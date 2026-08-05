"""Integración del Why Not Trade Engine (Bloque 14): motor real y API."""

import pytest
from app.config.settings import QuantRejectionsSettings, Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.engine.rejections import RejectionStore
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


async def _core(settings: Settings, store: RejectionStore):
    """Quant Core mínimo con el registro de rechazos cableado."""
    from app.engine.confidence import ConfidenceEngine
    from app.engine.consensus import ConsensusEngine
    from app.engine.decision_engine import DecisionEngine
    from app.engine.feature_store import FeatureStore
    from app.engine.filters import build_filter_chain
    from app.engine.market_context import MarketContextEngine
    from app.engine.regime_detection import RegimeDetector
    from app.engine.signal_engine import SignalEngine
    from app.engine.state_manager import SignalHistoryStore
    from app.engine.validators import SignalValidator

    from tests.unit.quant_helpers import make_candles, make_market, make_signal, make_trade

    quant = settings.quant
    market = make_market(
        candles=make_candles([100.0, 101.0] * 30), trades=[make_trade(price=100.5)]
    )
    features = FeatureStore(market)
    regime = RegimeDetector(market, quant.regime)
    context = MarketContextEngine(market, features, regime, quant.context)
    history = SignalHistoryStore()
    signals = SignalEngine(SignalValidator(), history, None)
    # Una senal floja pero real: sin senales activas no hay oportunidad que
    # rechazar, y el motor ni siquiera llega a evaluar umbrales ni filtros.
    await signals.submit(make_signal(symbol="BTCUSDM", score=20.0, confidence=0.2))
    return DecisionEngine(
        signals,
        context,
        ConsensusEngine(quant.consensus),
        ConfidenceEngine(quant.confidence),
        build_filter_chain(quant.filters, drawdown_reader=lambda: 0.0, recent_decisions=lambda: []),
        history,
        quant.consensus,
        None,
        None,
        store,
    )


@pytest.mark.asyncio
async def test_a_real_rejection_is_recorded_with_its_full_breakdown() -> None:
    # Sin señales no hay consenso: la decisión se rechaza y el desglose tiene
    # que decir exactamente qué puerta lo paró.
    settings = Settings()
    store = RejectionStore(QuantRejectionsSettings(persist=False))
    engine = await _core(settings, store)

    decision = await engine.evaluate("BTCUSDm")

    assert decision.accepted is False
    recorded = store.recent()
    assert len(recorded) == 1
    assert recorded[0].decision_id == decision.decision_id
    assert recorded[0].blocked_by
    assert recorded[0].primary_reason
    assert recorded[0].final_score == 0.0


@pytest.mark.asyncio
async def test_the_breakdown_carries_the_thresholds_that_were_actually_applied() -> None:
    settings = Settings()
    store = RejectionStore(QuantRejectionsSettings(persist=False))
    engine = await _core(settings, store)
    await engine.evaluate("BTCUSDm")

    record = store.recent()[0]
    thresholds = {g.name: g for g in record.gates if g.kind == "threshold"}
    assert "min_score" in thresholds
    assert thresholds["min_score"].required == settings.quant.consensus.min_score
    assert "confidence_breakdown" in record.evidence


@pytest.mark.asyncio
async def test_every_filter_in_the_chain_appears_in_the_record() -> None:
    settings = Settings()
    store = RejectionStore(QuantRejectionsSettings(persist=False))
    engine = await _core(settings, store)
    await engine.evaluate("BTCUSDm")

    names = {g.name for g in store.recent()[0].gates if g.kind == "filter"}
    # Los filtros de los bloques 3 y 7 solo entran en la cadena si su motor
    # esta cableado (aqui no lo esta), y esa ausencia es deliberada: fuera de la
    # cadena no aparecen en el registro, que es lo mismo que en la explicacion
    # de la decision.
    assert {"spread", "session", "liquidity", "news", "drawdown", "correlation"} <= names
    assert "position_quality" not in names


@pytest.mark.asyncio
async def test_an_evaluation_without_signals_is_counted_not_stored() -> None:
    # Dos simbolos por segundo son ~170.000 filas al dia de un motivo que no es
    # un motivo: ahogaria los rechazos reales.
    from app.engine.confidence import ConfidenceEngine
    from app.engine.consensus import ConsensusEngine
    from app.engine.decision_engine import DecisionEngine
    from app.engine.feature_store import FeatureStore
    from app.engine.filters import build_filter_chain
    from app.engine.market_context import MarketContextEngine
    from app.engine.regime_detection import RegimeDetector
    from app.engine.signal_engine import SignalEngine
    from app.engine.state_manager import SignalHistoryStore
    from app.engine.validators import SignalValidator

    from tests.unit.quant_helpers import make_candles, make_market

    settings = Settings()
    quant = settings.quant
    store = RejectionStore(QuantRejectionsSettings(persist=False))
    market = make_market(candles=make_candles([100.0] * 60))
    features = FeatureStore(market)
    history = SignalHistoryStore()
    engine = DecisionEngine(
        SignalEngine(SignalValidator(), history, None),
        MarketContextEngine(market, features, RegimeDetector(market, quant.regime), quant.context),
        ConsensusEngine(quant.consensus),
        ConfidenceEngine(quant.confidence),
        build_filter_chain(quant.filters, drawdown_reader=lambda: 0.0, recent_decisions=lambda: []),
        history,
        quant.consensus,
        None,
        None,
        store,
    )

    await engine.evaluate("BTCUSDm")

    assert store.recent() == []
    assert store.summary()["no_opportunity"] == 1


@pytest.mark.asyncio
async def test_the_engine_works_without_the_store_wired() -> None:
    # El registro es observación: su ausencia no puede cambiar la decisión.
    settings = Settings()
    with_store = RejectionStore(QuantRejectionsSettings(persist=False))
    a = await (await _core(settings, with_store)).evaluate("BTCUSDm")
    b = await (
        await _core(settings, RejectionStore(QuantRejectionsSettings(enabled=False)))
    ).evaluate("BTCUSDm")
    assert a.accepted == b.accepted
    assert a.action == b.action


def _client(store: RejectionStore | None) -> TestClient:
    container = Container()
    if store is not None:
        container.register_instance(RejectionStore, store)
    return TestClient(create_app(Settings(), container=container))


def test_the_endpoints_serve_the_dashboard_panel() -> None:
    from tests.unit.test_rejections import _record

    store = RejectionStore(QuantRejectionsSettings(persist=False))
    store.record(_record("d-1", "spread"))
    store.record(_record("d-2", "spread", "min_score"))
    client = _client(store)

    assert client.get("/api/rejections/status").json()["recorded"] == 2

    summary = client.get("/api/rejections/summary").json()
    assert summary["blocked_by"]["spread"] == 2
    assert summary["sole_blocker"]["spread"] == 1

    body = client.get("/api/rejections?limit=1").json()
    assert len(body["rejections"]) == 1
    assert body["rejections"][0]["gates"]


def test_rejection_endpoints_answer_503_when_not_wired() -> None:
    assert _client(None).get("/api/rejections/status").status_code == 503

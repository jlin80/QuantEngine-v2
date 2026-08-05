"""Integración del Microstructure Engine (Bloque 3).

Las tres fronteras: el Feature Store, el filtro del Decision Engine y la API.
La cuarta —el collector— se cubre verificando que el observador recibe lo que
el collector procesa, sin montar el feed entero.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import QuantMicrostructureSettings, Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.engine.feature_store import FeatureStore
from app.engine.filters import MicrostructureFilter, build_filter_chain
from app.engine.microstructure import MicrostructureEngine
from app.engine.microstructure.features import register_microstructure_features
from app.engine.models import ConsensusResult, Direction, MarketContext
from fastapi.testclient import TestClient

from tests.unit.quant_helpers import make_candles, make_market
from tests.unit.test_microstructure import _book, _feed

pytestmark = pytest.mark.integration

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _engine(**overrides) -> MicrostructureEngine:
    defaults = {"min_updates": 2}
    return MicrostructureEngine(QuantMicrostructureSettings(**{**defaults, **overrides}))


def _fed_engine() -> MicrostructureEngine:
    engine = _engine()
    _feed(engine, _book([(99.0, 30.0)], [(101.0, 10.0)]))
    return engine


# ---------------------------------------------------------------------------
# Feature Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_microstructure_metrics_become_regular_features() -> None:
    store = FeatureStore(make_market(candles=make_candles([100.0] * 60)))
    register_microstructure_features(store, _fed_engine())
    assert "queue_imbalance" in store.available
    assert "microstructure" in store.available_objects


@pytest.mark.asyncio
async def test_an_unobservable_metric_reads_as_none_like_any_missing_feature() -> None:
    # Un consumidor que ya sabe tratar un `atr` ausente sabe tratar esto.
    store = FeatureStore(make_market(candles=make_candles([100.0] * 60)))
    register_microstructure_features(store, _engine())
    assert await store.get("queue_imbalance", "BTCUSDm") is None
    assert await store.get("execution_pressure", "BTCUSDm") is None


@pytest.mark.asyncio
async def test_the_object_feature_carries_the_reason_it_is_not_observable() -> None:
    store = FeatureStore(make_market(candles=make_candles([100.0] * 60)))
    register_microstructure_features(store, _engine())
    snapshot = await store.get_object("microstructure", "BTCUSDm")
    assert snapshot.observable is False
    assert "sin libro" in snapshot.reason


# ---------------------------------------------------------------------------
# Filtro del Decision Engine
# ---------------------------------------------------------------------------


def _context() -> MarketContext:
    return MarketContext(symbol="BTCUSDT", generated_at=_START)


def _consensus() -> ConsensusResult:
    return ConsensusResult(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        method="weighted",
        score=70.0,
        agreement=0.8,
    )


def test_the_filter_fails_open_when_there_is_no_book_to_measure() -> None:
    # Un filtro que bloquea por ausencia de datos apagaría el motor entero con
    # el bróker actual, y lo haría sin errores: sólo dejando de operar.
    blocked = MicrostructureFilter(0.1, lambda _symbol: None)
    assert blocked.check(_context(), _consensus()).passed is True


def test_the_filter_blocks_only_on_a_measured_hostile_book() -> None:
    hostile = MicrostructureFilter(0.5, lambda _symbol: 0.9)
    result = hostile.check(_context(), _consensus())
    assert result.passed is False
    assert "presión de ejecución" in result.reason

    calm = MicrostructureFilter(0.5, lambda _symbol: 0.2)
    assert calm.check(_context(), _consensus()).passed is True


def test_the_filter_stays_out_of_the_chain_when_not_wired() -> None:
    # Fuera de la cadena no es lo mismo que dentro y pasando siempre: fuera, ni
    # siquiera aparece en la explicación de la decisión.
    from app.config.settings import QuantFiltersSettings

    settings = QuantFiltersSettings(enabled=["spread", "microstructure"])
    chain = build_filter_chain(settings, drawdown_reader=lambda: 0.0, recent_decisions=lambda: [])
    assert "microstructure" not in chain.names

    wired = build_filter_chain(
        settings,
        drawdown_reader=lambda: 0.0,
        recent_decisions=lambda: [],
        microstructure=(0.8, lambda _symbol: None),
    )
    assert "microstructure" in wired.names


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _client(engine: MicrostructureEngine | None) -> TestClient:
    container = Container()
    if engine is not None:
        container.register_instance(MicrostructureEngine, engine)
    return TestClient(create_app(Settings(), container=container))


def test_microstructure_endpoints_report_absence_instead_of_zeros() -> None:
    client = _client(_engine())
    body = client.get("/api/microstructure/BTCUSDm").json()
    assert body["observable"] is False
    assert body["queue_imbalance"] is None
    assert "sin libro" in body["reason"]


def test_microstructure_endpoints_expose_a_measured_book() -> None:
    client = _client(_fed_engine())
    status = client.get("/api/microstructure/status").json()
    assert status["symbols"] == ["BTCUSDT"]
    assert status["events"]["BTCUSDT"] == 2


def test_microstructure_endpoints_answer_503_when_not_wired() -> None:
    assert _client(None).get("/api/microstructure/status").status_code == 503


# ---------------------------------------------------------------------------
# Camino del collector
# ---------------------------------------------------------------------------


def test_the_observer_contract_matches_what_the_collector_calls() -> None:
    # El collector conoce un Protocol, no el motor. Si la firma se separa, esto
    # falla aquí y no en producción con el feed en marcha.
    from app.market.collector.collector import BookObserver

    engine: BookObserver = _engine()
    assert hasattr(engine, "observe_delta")
    assert hasattr(engine, "observe_trade")


def test_stale_events_expire_from_the_window() -> None:
    engine = _fed_engine()
    fresh = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3))
    stale = engine.snapshot("BTCUSDT", now=_START + timedelta(hours=2))
    assert fresh.observable is True
    assert stale.observable is False

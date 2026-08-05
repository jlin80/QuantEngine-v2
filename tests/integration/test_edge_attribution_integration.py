"""Integración del Edge Attribution Engine (Bloque 2).

Las dos fronteras del bloque: la captura sobre el Event Bus (con un Feature
Store real) y los endpoints que alimentan el panel del dashboard.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from app.config.settings import QuantAttributionSettings, Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.attribution import (
    EdgeAttributionEngine,
    FactorCapture,
    FactorSnapshotStore,
)
from app.engine.events import AttributionReportGenerated, DecisionGenerated
from app.engine.feature_store import FeatureStore
from app.engine.models import Decision, DecisionAction
from fastapi.testclient import TestClient

from tests.unit.quant_helpers import make_candles, make_market, make_trade
from tests.unit.test_edge_attribution import _informative_sample

pytestmark = pytest.mark.integration

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _decision(decision_id: str) -> Decision:
    return Decision(
        decision_id=decision_id,
        symbol="BTCUSDm",
        timestamp=_START,
        action=DecisionAction.OPEN_LONG,
        accepted=True,
        score=70.0,
        confidence=0.65,
        agreement=0.8,
        confidence_breakdown={"liquidity": 0.9, "confirmation": 0.4},
        context_summary={"regime": "trending", "volatility": "normal", "sessions": ["london"]},
    )


def _event(decision_id: str) -> DecisionGenerated:
    return DecisionGenerated(
        source="decision_engine",
        decision_id=decision_id,
        symbol="BTCUSDm",
        action="buy",
        accepted=True,
        score=70.0,
        confidence=0.65,
        summary="ok",
        strategy="alpha",
        strategy_category="momentum",
    )


def _capture(store: FactorSnapshotStore, bus: EventBus | None = None) -> FactorCapture:
    market = make_market(
        candles=make_candles([100.0, 101.0] * 25), trades=[make_trade(price=100.5)]
    )
    return FactorCapture(FeatureStore(market), lambda: [_decision("d-1")], store, bus)


# ---------------------------------------------------------------------------
# Captura sobre el bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_decision_on_the_bus_gets_its_factors_photographed() -> None:
    bus = EventBus()
    await bus.start()
    store = FactorSnapshotStore(None, persist=False)
    capture = _capture(store, bus)
    try:
        await capture.start()
        await bus.publish(_event("d-1"))
        await asyncio.sleep(0.05)
    finally:
        await capture.stop()
        await bus.stop()

    snapshot = store.index()["d-1"]
    assert snapshot.labels["strategy"] == "alpha"
    assert snapshot.labels["regime"] == "trending"
    assert snapshot.labels["session"] == "london"
    assert snapshot.numeric["liquidity"] == 0.9
    assert snapshot.numeric["score"] == 70.0
    # El desfase entre decidir y fotografiar se mide, no se finge cero.
    assert "lag_seconds" in snapshot.numeric


@pytest.mark.asyncio
async def test_a_decision_without_its_full_record_is_still_photographed() -> None:
    # El buffer de decisiones es acotado: una decisión que ya rotó no puede
    # dejar sin foto a la operación que salga de ella.
    store = FactorSnapshotStore(None, persist=False)
    capture = _capture(store)
    await capture.on_decision(_event("d-desconocida"))
    snapshot = store.index()["d-desconocida"]
    assert snapshot.labels["regime"] == "unknown"
    assert snapshot.numeric["liquidity"] is None
    assert snapshot.numeric["score"] == 70.0


@pytest.mark.asyncio
async def test_a_capture_failure_never_propagates_to_the_bus() -> None:
    # La atribución es observación: una observación que tumba el bus del motor
    # no vale lo que cuesta.
    class _Broken(FeatureStore):
        async def get(self, name, symbol, **params):  # type: ignore[no-untyped-def]
            raise RuntimeError("feature store caído")

    market = make_market(candles=make_candles([100.0] * 50))
    store = FactorSnapshotStore(None, persist=False)
    capture = FactorCapture(_Broken(market), lambda: [], store, None)
    await capture.on_decision(_event("d-1"))
    assert capture.status()["missed"] == 1
    assert capture.status()["captured"] == 0


@pytest.mark.asyncio
async def test_a_non_decision_event_is_ignored_by_the_capture() -> None:
    store = FactorSnapshotStore(None, persist=False)
    capture = _capture(store)
    await capture.on_decision(
        AttributionReportGenerated(trades=0, matched=0, factors=0, top_factor="")
    )
    assert capture.status()["captured"] == 0


# ---------------------------------------------------------------------------
# Ciclo y evento
# ---------------------------------------------------------------------------


def _engine(bus: EventBus | None = None) -> EdgeAttributionEngine:
    trades, snapshots = _informative_sample()
    store = FactorSnapshotStore(None, persist=False)
    for snapshot in snapshots:
        store.record(snapshot)
    settings = QuantAttributionSettings(min_sample=9, min_bucket=3)
    return EdgeAttributionEngine(settings, lambda: list(trades), store, bus)


@pytest.mark.asyncio
async def test_a_cycle_announces_its_report_on_the_bus() -> None:
    bus = EventBus()
    await bus.start()
    seen: list[AttributionReportGenerated] = []

    async def collect(event: AttributionReportGenerated) -> None:
        seen.append(event)

    bus.subscribe(collect, AttributionReportGenerated)
    try:
        await _engine(bus).run_cycle()
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert len(seen) == 1
    assert seen[0].matched == 30
    assert seen[0].top_factor


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _client(engine: EdgeAttributionEngine | None) -> TestClient:
    container = Container()
    if engine is not None:
        container.register_instance(EdgeAttributionEngine, engine)
    return TestClient(create_app(Settings(), container=container))


def test_attribution_endpoints_serve_the_dashboard_panel() -> None:
    engine = _engine()
    client = _client(engine)

    assert client.get("/api/attribution/report").json()["status"] == "pending"

    cycle = client.post("/api/attribution/cycle").json()
    assert cycle["matched"] == 30
    assert cycle["factors"]
    assert "no causa" in cycle["caveat"]

    assert client.get("/api/attribution/status").json()["cycles"] == 1

    trades = client.get("/api/attribution/trades?limit=2").json()["trades"]
    assert [t["trade_id"] for t in trades] == ["t-29", "t-28"]

    one = client.get("/api/attribution/trades/t-29").json()
    assert one["contributions"]
    assert "residual_r" in one

    assert client.get("/api/attribution/trades/no-existe").status_code == 404


def test_attribution_endpoints_answer_503_when_not_wired() -> None:
    assert _client(None).get("/api/attribution/status").status_code == 503

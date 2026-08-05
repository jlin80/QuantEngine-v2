"""Integración del Edge Research Engine (Bloque 1).

Cubre las tres fronteras que el bloque cruza: el Event Bus, la API del
dashboard y el gobierno del Meta Strategy Manager.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import MLMetaStrategySettings, QuantEdgeResearchSettings, Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.edge_research import EdgeReportHistory, EdgeResearchEngine
from app.engine.evaluation.outcomes import VirtualOutcome, VirtualOutcomeStore
from app.engine.events import EdgeDecayDetected, EdgeReportGenerated
from app.ml.meta.manager import MetaStrategyManager
from app.ml.services import EdgeHealthStats, StrategyIntelligence
from fastapi.testclient import TestClient

from tests.unit.ml_helpers import strategy_trades

pytestmark = pytest.mark.integration

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _outcome(index: int, r: float, strategy: str = "alpha") -> VirtualOutcome:
    opened = _START + timedelta(minutes=index)
    return VirtualOutcome(
        signal_id=f"{strategy}-{index}",
        strategy=strategy,
        symbol="BTCUSDm",
        direction="long",
        entry=100.0,
        stop=99.0,
        target=102.0,
        r_multiple=r,
        outcome="win" if r > 0 else "loss",
        false_signal=False,
        opened_at=opened,
        closed_at=opened + timedelta(minutes=5),
        confidence=0.6,
    )


def _dying() -> list[VirtualOutcome]:
    """Una estrategia cuyo edge se apaga a lo largo de la ventana."""
    values = [1.0] * 10 + [0.5] * 10 + [-0.5] * 10 + [-1.0] * 10
    return [_outcome(i, v) for i, v in enumerate(values)]


def _engine(outcomes, bus=None, tmp_path=None) -> EdgeResearchEngine:
    settings = QuantEdgeResearchSettings(min_sample=10, blocks=4)
    history = EdgeReportHistory(
        None if tmp_path is None else tmp_path / "edge.jsonl",
        persist=tmp_path is not None,
    )
    return EdgeResearchEngine(settings, lambda: list(outcomes), history, bus)


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_research_cycle_publishes_its_report_on_the_bus() -> None:
    bus = EventBus()
    await bus.start()
    seen: list[EdgeReportGenerated] = []

    async def collect(event: EdgeReportGenerated) -> None:
        seen.append(event)

    bus.subscribe(collect, EdgeReportGenerated)
    try:
        await _engine(_dying(), bus).run_cycle()
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert len(seen) == 1
    assert seen[0].strategies == 1
    assert seen[0].degrading == ("alpha",)


@pytest.mark.asyncio
async def test_decay_is_announced_on_the_transition_not_on_every_cycle() -> None:
    # Reanunciar el mismo deterioro cada ciclo convierte la alarma en ruido de
    # fondo, que es exactamente como se dejan de leer las alarmas.
    bus = EventBus()
    await bus.start()
    seen: list[EdgeDecayDetected] = []

    async def collect(event: EdgeDecayDetected) -> None:
        seen.append(event)

    bus.subscribe(collect, EdgeDecayDetected)
    try:
        engine = _engine(_dying(), bus)
        await engine.run_cycle()
        await engine.run_cycle()
        await engine.run_cycle()
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert len(seen) == 1
    assert seen[0].strategy == "alpha"
    assert seen[0].edge_decay > 0.0
    assert seen[0].reasons


# ---------------------------------------------------------------------------
# API del dashboard
# ---------------------------------------------------------------------------


def _client(engine: EdgeResearchEngine | None) -> TestClient:
    container = Container()
    if engine is not None:
        container.register_instance(EdgeResearchEngine, engine)
    app = create_app(Settings(), container=container)
    return TestClient(app)


def test_edge_endpoints_expose_report_history_and_manual_cycle(tmp_path) -> None:
    engine = _engine(_dying(), tmp_path=tmp_path)
    client = _client(engine)

    assert client.get("/api/edge/report").json()["status"] == "pending"

    cycle = client.post("/api/edge/cycle")
    assert cycle.status_code == 200
    assert cycle.json()["degrading"] == ["alpha"]

    status = client.get("/api/edge/status").json()
    assert status["cycles"] == 1
    assert status["samples"]["alpha"] == 40

    strategy = client.get("/api/edge/strategies/alpha").json()
    assert strategy["status"] == "degrading"
    assert strategy["edge_decay"] > 0.0

    history = client.get("/api/edge/strategies/alpha/history").json()
    assert len(history["points"]) == 1

    assert client.get("/api/edge/strategies/no-existe").status_code == 404


def test_edge_endpoints_answer_503_when_the_engine_is_not_wired() -> None:
    assert _client(None).get("/api/edge/status").status_code == 503


# ---------------------------------------------------------------------------
# Meta Strategy Manager
# ---------------------------------------------------------------------------


def _labeled(strategy: str, count: int) -> list[tuple[str, object]]:
    """Operaciones ejecutadas y etiquetadas, suficientes para gobernar el peso."""
    return [(strategy, trade) for trade in strategy_trades(strategy, count)]


def _manager() -> MetaStrategyManager:
    return MetaStrategyManager(MLMetaStrategySettings(), StrategyIntelligence())


def test_a_decaying_edge_damps_the_target_weight() -> None:
    trades = _labeled("alpha", 30)
    healthy = _manager().evaluate(trades).weights["alpha"]
    damped = _manager().evaluate(
        trades,
        None,
        {
            "alpha": EdgeHealthStats(
                strategy="alpha",
                status="degrading",
                health_score=10.0,
                factor=0.5,
                sample=40,
            )
        },
    )
    assert damped.weights["alpha"] < healthy
    assert damped.decisions[0]["edge"]["status"] == "degrading"


def test_edge_health_alone_never_disables_a_strategy() -> None:
    # Es una métrica de tendencia sobre decenas de resoluciones: puede frenar un
    # peso, no apagar una estrategia. Apagar sigue exigiendo muestra ejecutada.
    report = _manager().evaluate(
        _labeled("alpha", 30),
        None,
        {
            "alpha": EdgeHealthStats(
                strategy="alpha",
                status="degrading",
                health_score=0.0,
                factor=0.5,
                sample=40,
            )
        },
    )
    assert report.active == ["alpha"]
    assert report.disabled == []


def test_governance_without_edge_evidence_behaves_exactly_as_before() -> None:
    trades = _labeled("alpha", 30)
    assert _manager().evaluate(trades).weights == _manager().evaluate(trades, None, {}).weights


# ---------------------------------------------------------------------------
# Compatibilidad del store de resultados virtuales
# ---------------------------------------------------------------------------


def test_outcome_rows_written_before_this_block_still_load(tmp_path) -> None:
    path = tmp_path / "virtual_outcomes.jsonl"
    legacy = _outcome(0, 1.0).to_dict()
    del legacy["confidence"]  # fila anterior al Bloque 1
    path.write_text(__import__("json").dumps(legacy) + "\n", encoding="utf-8")
    loaded = list(VirtualOutcomeStore(path).load())
    assert len(loaded) == 1
    assert loaded[0].confidence is None

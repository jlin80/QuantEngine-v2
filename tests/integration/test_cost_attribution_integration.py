"""Integración del Cost Attribution Engine (Bloque 9): cableado real y API."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.execution.costs import CostAttributionEngine
from fastapi.testclient import TestClient

from tests.unit.test_cost_attribution import _engine, _outcome, _trade

pytestmark = pytest.mark.integration


def _client(wired: bool) -> TestClient:
    container = Container()
    if wired:
        trades = [_trade(i, commission=2.0, slippage_bps=8.0, day=1) for i in range(3)]
        trades += [_trade(10 + i, commission=2.0, extra_cost=6.0, day=2) for i in range(2)]
        container.register_instance(
            CostAttributionEngine, _engine(trades, [_outcome("s-win", 1.5)])
        )
    return TestClient(create_app(Settings(), container=container))


def test_the_report_endpoint_splits_the_gross_into_its_parts() -> None:
    body = _client(True).get("/api/costs/report").json()
    assert body["total"]["commission"] == 10.0
    assert body["total"]["slippage"] > 0.0
    assert body["total"]["latency"] is None
    assert body["total"]["opportunity"] == 1.5


def test_the_daily_endpoint_carries_the_notes_too() -> None:
    # Un informe diario que oculte "hay costes que no se están midiendo" invita
    # a leer el reparto como si estuviera completo.
    body = _client(True).get("/api/costs/daily").json()
    assert set(body["daily"]) == {"2026-08-01", "2026-08-02"}
    assert any("latencia" in note for note in body["notes"])


def test_the_engine_is_wired_from_the_real_composition_root() -> None:
    from app.engine.bootstrap import build_container

    settings = Settings()
    settings.market.enabled = True
    settings.quant.enabled = True
    settings.execution.enabled = True
    assert build_container(settings).contains(CostAttributionEngine)


def test_costs_endpoints_answer_503_when_not_wired() -> None:
    assert _client(False).get("/api/costs/report").status_code == 503

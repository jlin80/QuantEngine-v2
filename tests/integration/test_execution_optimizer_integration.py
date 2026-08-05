"""Integración del Execution Optimizer (Bloque 6): cableado y API."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.execution.optimizer import ExecutionOptimizer
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _client(wired: bool) -> TestClient:
    container = Container()
    if wired:
        from tests.unit.test_execution_optimizer import _optimizer

        container.register_instance(ExecutionOptimizer, _optimizer())
    return TestClient(create_app(Settings(), container=container))


def test_the_plan_endpoint_quotes_without_sending_any_order() -> None:
    client = _client(True)
    body = client.get(
        "/api/optimizer/plan",
        params={"symbol": "BTCUSDm", "quantity": 0.01, "spread_bps": 4.0, "urgency": 1.0},
    ).json()
    assert body["chosen"]["tactic"] == "market"
    assert len(body["alternatives"]) == 2
    assert body["reason"]


def test_urgency_changes_the_chosen_tactic_through_the_api() -> None:
    client = _client(True)
    calm = client.get(
        "/api/optimizer/plan",
        params={"symbol": "BTCUSDm", "quantity": 0.01, "spread_bps": 4.0, "urgency": 0.0},
    ).json()
    assert calm["chosen"]["tactic"] != "market"


def test_the_optimizer_is_wired_from_the_real_composition_root() -> None:
    from app.engine.bootstrap import build_container

    settings = Settings()
    settings.market.enabled = True
    settings.quant.enabled = True
    settings.execution.enabled = True
    container = build_container(settings)
    assert container.contains(ExecutionOptimizer)


def test_optimizer_endpoint_answers_503_when_not_wired() -> None:
    assert (
        _client(False).get("/api/optimizer/plan", params={"symbol": "BTCUSDm"}).status_code == 503
    )

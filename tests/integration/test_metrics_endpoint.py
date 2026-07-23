"""Integración: /metrics sobre el motor real, tal como lo scrapea Prometheus."""

import pytest
from app.config.settings import Settings
from app.dashboard.api.main import create_app
from app.engine.bootstrap import build_container
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture()
def wired_settings(settings: Settings, tmp_path) -> Settings:
    updated = settings.model_copy(deep=True)
    updated.production.enabled = True
    updated.production.audit_path = tmp_path / "audit.jsonl"
    updated.production.approval_path = tmp_path / "approval.json"
    updated.production.kill_switch.state_path = tmp_path / "kill.json"
    updated.production.recovery.snapshot_path = tmp_path / "state.json"
    updated.execution.enabled = True
    updated.execution.persist_journal = False
    updated.market.enabled = True
    return updated


@pytest.fixture()
def client(wired_settings: Settings):
    app = create_app(wired_settings, build_container(wired_settings))
    with TestClient(app) as test_client:
        yield test_client


def test_metrics_is_served_where_prometheus_expects_it(client: TestClient):
    """docker/prometheus/prometheus.yml scrapea /metrics, no /api/metrics."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]


def test_payload_exposes_the_engine_state(client: TestClient):
    body = client.get("/metrics").text
    for metric in (
        "quantengine_portfolio_equity",
        "quantengine_open_positions",
        "quantengine_kill_switch_active",
        "quantengine_safe_mode_active",
        "quantengine_live_enabled",
        "quantengine_broker_info",
    ):
        assert metric in body, metric


def test_live_enabled_is_zero(client: TestClient):
    """El invariante de la fase, ahora también observable desde Grafana."""
    body = client.get("/metrics").text
    assert "quantengine_live_enabled 0.0" in body
    assert 'quantengine_broker_info{broker="paper"} 1.0' in body


def test_every_series_declares_help_and_type(client: TestClient):
    body = client.get("/metrics").text
    declared: set[str] = set()
    samples: set[str] = set()
    for line in body.splitlines():
        if line.startswith("# TYPE "):
            declared.add(line.split()[2])
        elif line and not line.startswith("#"):
            name = line.split("{")[0].split(" ")[0]
            samples.add(name.removesuffix("_bucket").removesuffix("_sum").removesuffix("_count"))
    assert samples <= declared


def test_endpoint_degrades_without_a_container(wired_settings: Settings):
    """Un motor apagado devuelve payload vacío pero válido, nunca un 500."""
    app = create_app(wired_settings, container=None)
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200

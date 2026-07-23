"""Integración: API FastAPI con los servicios reales inyectados."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.monitoring.health import HealthMonitor
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(settings: Settings):
    container = Container()
    bus = EventBus()
    container.register_instance(EventBus, bus)
    container.register_instance(HealthMonitor, HealthMonitor(settings.health, bus))
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "testing"


def test_system_status_endpoint(client: TestClient):
    response = client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("healthy", "degraded", "unhealthy")
    assert "cpu_percent" in body
    assert "event_bus" in body


def test_system_info_endpoint(client: TestClient):
    response = client.get("/api/system/info")
    assert response.status_code == 200
    body = response.json()
    assert "XAUUSD" in body["instruments"]


def test_system_status_without_container(settings: Settings):
    app = create_app(settings, container=None)
    with TestClient(app) as client:
        response = client.get("/api/system/status")
    assert response.status_code == 503

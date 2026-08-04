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


def _bus_subscribers(bus: EventBus) -> int:
    """Suscriptores vivos, segun la propia contabilidad del bus."""
    return bus.stats.subscribers


def test_the_event_stream_releases_its_subscription_on_disconnect(settings: Settings):
    """Regresion: cada cliente que se iba dejaba un zombi suscrito al bus.

    El bucle de envio solo salia con `WebSocketDisconnect`, pero cuando el
    cliente desaparece sin cierre limpio `send_json` **no lanza** — asyncio ve
    el transporte muerto, descarta el envio y vuelve. Resultado en produccion:
    un WARNING de asyncio por cada evento del motor (1.500 de cada 2.000 lineas
    de log) y una suscripcion viva por cada recarga del dashboard.
    """
    container = Container()
    bus = EventBus()
    container.register_instance(EventBus, bus)
    container.register_instance(HealthMonitor, HealthMonitor(settings.health, bus))
    app = create_app(settings, container)

    with TestClient(app) as client:
        before = _bus_subscribers(bus)
        with client.websocket_connect("/ws/events"):
            during = _bus_subscribers(bus)
        after = _bus_subscribers(bus)

    assert during == before + 1, "el cliente conectado debe estar suscrito"
    assert after == before, "al desconectar no puede quedar suscripcion viva"


def test_repeated_connections_do_not_accumulate_subscriptions(settings: Settings):
    """El dashboard se recarga a menudo; cada recarga no puede dejar residuo."""
    container = Container()
    bus = EventBus()
    container.register_instance(EventBus, bus)
    container.register_instance(HealthMonitor, HealthMonitor(settings.health, bus))
    app = create_app(settings, container)

    with TestClient(app) as client:
        baseline = _bus_subscribers(bus)
        for _ in range(5):
            with client.websocket_connect("/ws/events"):
                pass
        assert _bus_subscribers(bus) == baseline

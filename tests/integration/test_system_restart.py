"""Reinicio del motor desde el dashboard: guardas, auditoria y efecto.

No toca el planificador de Windows: el motor se detiene de forma ordenada y el
watchdog lo relanza. Por eso las guardas importan mas de lo normal — un clic
suelto no puede dejar posiciones vivas sin gestion.
"""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.engine import QuantEngine
from app.execution.api import ExecutionCore
from app.monitoring.health import HealthMonitor
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _FakeEngine:
    def __init__(self):
        self.stopped = False

    def request_stop(self):
        self.stopped = True


class _FakeCore:
    def __init__(self, positions):
        self._positions = positions

    def open_positions(self):
        return self._positions


def _client(settings: Settings, *, engine=None, positions=None):
    container = Container()
    bus = EventBus()
    container.register_instance(EventBus, bus)
    container.register_instance(HealthMonitor, HealthMonitor(settings.health, bus))
    if engine is not None:
        container.register_instance(QuantEngine, engine)
    if positions is not None:
        container.register_instance(ExecutionCore, _FakeCore(positions))
    return TestClient(create_app(settings, container))


def test_restart_requires_explicit_confirmation(settings: Settings):
    engine = _FakeEngine()
    with _client(settings, engine=engine, positions=[]) as client:
        assert client.post("/api/system/restart", json={}).status_code == 400
        assert client.post("/api/system/restart", json={"confirm": False}).status_code == 400
    assert engine.stopped is False  # nunca se detuvo


def test_restart_refuses_with_open_positions(settings: Settings):
    """Reiniciar con posiciones vivas las deja sin trailing ni break-even."""
    engine = _FakeEngine()
    positions = [{"symbol": "ETHUSDM"}, {"symbol": "USTECM"}]
    with _client(settings, engine=engine, positions=positions) as client:
        response = client.post("/api/system/restart", json={"confirm": True})

    assert response.status_code == 409
    assert "ETHUSDM" in response.json()["detail"]
    assert engine.stopped is False


def test_restart_proceeds_when_flat_and_confirmed(settings: Settings):
    engine = _FakeEngine()
    with _client(settings, engine=engine, positions=[]) as client:
        response = client.post("/api/system/restart", json={"confirm": True})

    assert response.status_code == 200
    assert response.json()["restarting"] is True


def test_restart_is_audited(settings: Settings):
    from app.dashboard.api.audit import audit_log

    engine = _FakeEngine()
    before = len(audit_log.recent(limit=500))
    with _client(settings, engine=engine, positions=[]) as client:
        client.post("/api/system/restart", json={"confirm": True, "reason": "config change"})

    entries = audit_log.recent(limit=500)
    assert len(entries) > before
    assert any(e.get("action") == "system.restart" for e in entries)


def test_restart_without_engine_is_unavailable(settings: Settings):
    """Sin motor registrado se degrada con 503, no con un 500."""
    with _client(settings, positions=[]) as client:
        assert client.post("/api/system/restart", json={"confirm": True}).status_code == 503

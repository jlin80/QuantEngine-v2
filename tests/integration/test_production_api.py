"""Integración: endpoints de producción sobre la app FastAPI real.

Lo que estas pruebas cuidan sobre todo es el invariante de la fase: **ninguna
llamada HTTP puede habilitar live trading**.
"""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.production.api import ProductionAPI
from app.production.audit import AuditLog
from app.production.kill_switch import KillSwitchController
from app.production.live import ApprovalStore, LiveGate, ModeResolver
from app.production.recovery import RecoveryService, StateSnapshotStore
from app.production.safe_mode import SafeModeController
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _build_production(settings: Settings, tmp_path) -> ProductionAPI:
    production = settings.production
    audit = AuditLog(tmp_path / "audit.jsonl")
    approvals = ApprovalStore(tmp_path / "approval.json")
    gate = LiveGate(production.live, approval_lookup=approvals.is_valid_for)
    recovery_settings = production.recovery.model_copy(
        update={"snapshot_path": tmp_path / "state.json"}
    )
    kill_settings = production.kill_switch.model_copy(update={"state_path": tmp_path / "kill.json"})
    return ProductionAPI(
        settings=settings,
        gate=gate,
        approvals=approvals,
        mode=ModeResolver(settings, gate),
        safe_mode=SafeModeController(production.safe_mode, None, audit),
        kill_switch=KillSwitchController(kill_settings, None, None, audit),
        recovery=RecoveryService(
            recovery_settings, StateSnapshotStore(recovery_settings.snapshot_path)
        ),
        audit=audit,
    )


@pytest.fixture()
def client(settings: Settings, tmp_path):
    container = Container()
    container.register_instance(EventBus, EventBus())
    production = _build_production(settings, tmp_path)
    container.register_instance(ProductionAPI, production)
    container.register_instance(AuditLog, production.audit)
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_status_endpoint(client: TestClient):
    body = client.get("/api/production/status").json()
    assert body["mode"]["mode"] == "paper"
    assert body["mode"]["live_enabled"] is False


def test_live_report_explains_every_criterion(client: TestClient):
    body = client.get("/api/production/live/report").json()
    assert body["approved"] is False
    assert body["failed"] > 0
    assert all(check["detail"] for check in body["checks"])


def test_enable_live_is_rejected_and_audited(client: TestClient):
    body = client.post("/api/production/live/enable", json={"actor": "jlin"}).json()
    assert body["enabled"] is False
    assert body["blocking_reason"]

    audit = client.get("/api/audit").json()["entries"]
    assert any(entry["action"] == "live.enable_rejected" for entry in audit)


def test_approving_alone_does_not_enable_live(client: TestClient):
    """Aprobar satisface un criterio; no abre la puerta por sí solo."""
    approved = client.post(
        "/api/production/live/approve", json={"actor": "jlin", "reason": "revisado"}
    ).json()
    assert approved["approved"] is False

    enabled = client.post("/api/production/live/enable", json={"actor": "jlin"}).json()
    assert enabled["enabled"] is False
    assert client.get("/api/production/status").json()["mode"]["mode"] == "paper"


def test_kill_switch_requires_a_reason(client: TestClient):
    assert client.post("/api/production/kill_switch", json={"actor": "jlin"}).status_code == 422


def test_kill_switch_engage_and_release(client: TestClient):
    engaged = client.post(
        "/api/production/kill_switch", json={"actor": "jlin", "reason": "prueba"}
    ).json()
    assert engaged["active"] is True

    assert (
        client.post(
            "/api/production/kill_switch/release", json={"actor": "jlin", "reason": ""}
        ).status_code
        == 422
    )

    released = client.post(
        "/api/production/kill_switch/release",
        json={"actor": "jlin", "reason": "causa resuelta"},
    ).json()
    assert released["active"] is False

    actions = {entry["action"] for entry in client.get("/api/audit").json()["entries"]}
    assert {"kill_switch.engaged", "kill_switch.released"} <= actions


def test_safe_mode_can_be_forced(client: TestClient):
    body = client.post("/api/production/safe_mode", json={"reason": "mantenimiento"}).json()
    assert body["active"] is True


def test_health_report(client: TestClient):
    body = client.get("/api/production/health").json()
    assert set(body) >= {"safe_mode", "kill_switch", "recovery", "mode"}


def test_endpoints_503_without_production_layer(settings: Settings):
    app = create_app(settings, container=None)
    with TestClient(app) as client:
        assert client.get("/api/production/status").status_code == 503

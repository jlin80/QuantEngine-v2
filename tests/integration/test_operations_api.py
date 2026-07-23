"""Integración: endpoints de operación de la Fase 9 sobre la app real.

Backups, seguridad, updates, mantenimiento y Notion — más el middleware de
seguridad (rate limiting + cabeceras). El invariante de la fase se mantiene:
nada aquí puede habilitar live trading.
"""

import pytest
from app.config.settings import Settings
from app.engine.bootstrap import build_container
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture()
def prod_settings(settings: Settings, tmp_path) -> Settings:
    """Testing settings with the production layer enabled under tmp_path."""
    production = settings.production.model_copy(
        update={
            "enabled": True,
            "state_dir": tmp_path,
            "audit_path": tmp_path / "audit.jsonl",
            "approval_path": tmp_path / "approval.json",
            "backup": settings.production.backup.model_copy(
                update={"backup_dir": tmp_path / "backups", "sources": [tmp_path / "audit.jsonl"]}
            ),
            "recovery": settings.production.recovery.model_copy(
                update={"snapshot_path": tmp_path / "state.json"}
            ),
            "kill_switch": settings.production.kill_switch.model_copy(
                update={"state_path": tmp_path / "kill.json"}
            ),
        }
    )
    return settings.model_copy(update={"production": production})


@pytest.fixture()
def client(prod_settings: Settings):
    container = build_container(prod_settings)
    from app.dashboard.api.main import create_app

    app: FastAPI = create_app(prod_settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_security_report_endpoint(client: TestClient):
    body = client.get("/api/security/report").json()
    assert "ok" in body
    assert "issues" in body


def test_backup_create_list_and_restore(client: TestClient):
    created = client.post("/api/backups/create", json={}).json()
    assert created["created"] is True
    backup_id = created["backup"]["backup_id"]

    listed = client.get("/api/backups").json()
    assert any(b["backup_id"] == backup_id for b in listed["backups"])

    restored = client.post("/api/backups/restore", json={"backup_id": backup_id}).json()
    assert restored["restored"] is True


def test_restore_unknown_backup_is_404(client: TestClient):
    response = client.post("/api/backups/restore", json={"backup_id": "nope"})
    assert response.status_code == 404


def test_updates_check_endpoint(client: TestClient):
    body = client.get("/api/updates/check").json()
    assert "current" in body


def test_maintenance_enter_exit(client: TestClient):
    entered = client.post("/api/maintenance/enter", json={"reason": "deploy"}).json()
    assert entered["active"] is True
    exited = client.post("/api/maintenance/exit", json={}).json()
    assert exited["active"] is False


def test_security_headers_present(client: TestClient):
    response = client.get("/api/security/report")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_notion_sync_without_backend_is_graceful(client: TestClient):
    body = client.post("/api/notion/sync", json={}).json()
    # Sin backend Notion, la cola está vacía: sincroniza sin error.
    assert body["synced"] is True

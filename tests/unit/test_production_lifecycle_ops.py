"""Licencias, mantenimiento y failover (Fase 9)."""

import time

from app.config.settings import (
    FailoverSettings,
    LicenseSettings,
    MaintenanceSettings,
)
from app.production.audit import AuditAction, AuditLog
from app.production.failover import FailoverCoordinator
from app.production.licenses import LicenseManager
from app.production.maintenance import MaintenanceManager

# ----------------------------------------------------------------------
# Licencias
# ----------------------------------------------------------------------


def test_disabled_license_is_unrestricted():
    manager = LicenseManager(LicenseSettings(enabled=False))
    status = manager.status()
    assert status == {"enabled": False, "valid": True, "plan": "unrestricted", "holder": ""}
    assert manager.is_feature_enabled("live_trading") is True


def test_license_validation_is_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    LicenseManager(LicenseSettings(enabled=False), audit).validate()
    assert audit.recent(action=str(AuditAction.LICENSE_VALIDATED))


# ----------------------------------------------------------------------
# Mantenimiento
# ----------------------------------------------------------------------


def test_maintenance_enter_exit_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    manager = MaintenanceManager(MaintenanceSettings(), audit)
    manager.enter(actor="jlin", reason="deploy")
    assert manager.active is True
    manager.exit(actor="jlin")
    assert manager.active is False
    assert audit.recent(action=str(AuditAction.MAINTENANCE_STARTED))
    assert audit.recent(action=str(AuditAction.MAINTENANCE_FINISHED))


def test_cleanup_removes_stale_files(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    old = spill / "old.tmp"
    old.write_text("x", encoding="utf-8")
    # Envejecer el fichero por debajo del corte.
    past = time.time() - 10 * 24 * 3600
    import os

    os.utime(old, (past, past))
    fresh = spill / "fresh.tmp"
    fresh.write_text("y", encoding="utf-8")

    manager = MaintenanceManager(
        MaintenanceSettings(cleanup_dirs=[spill], temp_max_age_hours=168.0)
    )
    result = manager.cleanup()
    assert result["removed"] == 1
    assert not old.exists()
    assert fresh.exists()


# ----------------------------------------------------------------------
# Failover
# ----------------------------------------------------------------------


def test_disabled_failover_is_always_primary():
    coordinator = FailoverCoordinator(FailoverSettings(enabled=False))
    assert coordinator.is_primary is True
    assert coordinator.should_run() is True


def test_lease_acquire_renew_and_takeover(tmp_path):
    lease = tmp_path / "leader.lease"
    settings_a = FailoverSettings(
        enabled=True, node_id="node-a", lease_path=lease, lease_ttl_seconds=30.0
    )
    settings_b = FailoverSettings(
        enabled=True, node_id="node-b", lease_path=lease, lease_ttl_seconds=30.0
    )
    node_a = FailoverCoordinator(settings_a)
    node_b = FailoverCoordinator(settings_b)

    # A toma el arriendo → primario; B lo ve fresco → standby.
    assert node_a.heartbeat(now=1000.0) is True
    assert node_b.heartbeat(now=1005.0) is False

    # A renueva mientras el arriendo esté vigente: sigue siendo primario.
    assert node_a.heartbeat(now=1010.0) is True

    # A muere: el arriendo caduca (ttl 30s) y B toma el relevo.
    assert node_b.heartbeat(now=1050.0) is True
    assert node_a.heartbeat(now=1055.0) is False


def test_failover_transition_is_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    coordinator = FailoverCoordinator(
        FailoverSettings(enabled=True, node_id="n1", lease_path=tmp_path / "l.lease"),
        audit,
    )
    coordinator.heartbeat(now=1.0)  # promoción
    assert audit.recent(action=str(AuditAction.FAILOVER_PROMOTED))

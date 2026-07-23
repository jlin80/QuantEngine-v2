"""Update Manager: comprobación contra manifiesto, migraciones y auditoría."""

import json

import pytest
from app import __version__
from app.config.settings import UpdateSettings
from app.production.audit import AuditAction, AuditLog
from app.production.updates import UpdateManager


def test_no_manifest_means_up_to_date(tmp_path):
    manager = UpdateManager(UpdateSettings(manifest_path=tmp_path / "none.json"))
    info = manager.check_for_updates()
    assert info.available is False
    assert info.current == __version__


def test_newer_manifest_reports_available(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"latest_version": "99.0.0", "notes": "big"}), encoding="utf-8")
    audit = AuditLog(tmp_path / "audit.jsonl")
    manager = UpdateManager(UpdateSettings(manifest_path=manifest), audit=audit)
    info = manager.check_for_updates()
    assert info.available is True
    assert info.latest == "99.0.0"
    assert audit.recent(action=str(AuditAction.UPDATE_CHECKED))


def test_pending_migrations_lists_scripts(tmp_path):
    migrations = tmp_path / "versions"
    migrations.mkdir()
    (migrations / "0001_init.py").write_text("x = 1", encoding="utf-8")
    (migrations / "__init__.py").write_text("", encoding="utf-8")
    manager = UpdateManager(UpdateSettings(), migrations_dir=migrations)
    assert manager.pending_migrations() == ["0001_init.py"]


def test_apply_and_rollback_are_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    manager = UpdateManager(UpdateSettings(), audit=audit)
    manager.apply(actor="jlin", target_version="9.9.9")
    manager.rollback(actor="jlin", to_version="0.8.0")
    assert audit.recent(action=str(AuditAction.UPDATE_APPLIED))
    assert audit.recent(action=str(AuditAction.UPDATE_ROLLED_BACK))


def test_apply_requires_actor(tmp_path):
    manager = UpdateManager(UpdateSettings())
    with pytest.raises(PermissionError):
        manager.apply(actor="  ", target_version="1.0.0")

"""Backups: creación, verificación, rotación y restauración segura."""

import pytest
from app.config.settings import BackupSettings
from app.production.backup import BackupService


def _service(tmp_path, **overrides) -> BackupService:
    data = tmp_path / "data"
    data.mkdir()
    (data / "audit.jsonl").write_text("evento-1\n", encoding="utf-8")
    state = data / "production"
    state.mkdir()
    (state / "state.json").write_text('{"k": 1}', encoding="utf-8")
    settings = BackupSettings(
        backup_dir=tmp_path / "backups",
        sources=[data / "audit.jsonl", state],
        **overrides,
    )
    return BackupService(settings)


def test_create_verify_and_list(tmp_path):
    service = _service(tmp_path)
    record = service.create()
    assert record.sha256, "el manifiesto guarda el hash del archivo"
    assert record.files, "incluye las fuentes existentes"
    assert service.verify(record.backup_id) is True
    assert [r.backup_id for r in service.list_backups()] == [record.backup_id]


def test_verify_detects_corruption(tmp_path):
    service = _service(tmp_path)
    record = service.create()
    # Corromper el archivo tras crear el backup invalida la verificación.
    from pathlib import Path

    Path(record.path).write_bytes(b"corrupto")
    assert service.verify(record.backup_id) is False


def test_restore_roundtrip(tmp_path):
    service = _service(tmp_path)
    record = service.create()
    destination = tmp_path / "restored"
    result = service.restore(record.backup_id, destination=destination)
    assert result["restored"] is True
    assert (destination / "audit.jsonl").read_text(encoding="utf-8") == "evento-1\n"


def test_restore_refuses_corrupt_backup(tmp_path):
    service = _service(tmp_path)
    record = service.create()
    from pathlib import Path

    Path(record.path).write_bytes(b"corrupto")
    with pytest.raises(ValueError, match="verification"):
        service.restore(record.backup_id, destination=tmp_path / "out")


def test_rotation_keeps_only_retention(tmp_path):
    service = _service(tmp_path, retention=2)
    ids = []
    for _ in range(4):
        # Timestamp con segundos: forzamos ids distintos avanzando el reloj.
        import time

        time.sleep(1.05)
        ids.append(service.create().backup_id)
    remaining = {r.backup_id for r in service.list_backups()}
    assert len(remaining) == 2
    assert remaining == set(ids[-2:]), "se conservan los dos más recientes"


def test_unknown_backup_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(FileNotFoundError):
        service.restore("backup_nope")

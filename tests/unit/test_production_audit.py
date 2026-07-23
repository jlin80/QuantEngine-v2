"""Auditoría: append-only, sin borrado y con memoria a través de reinicios."""

from pathlib import Path

from app.production.audit import AuditAction, AuditLog


def test_records_and_reads_newest_first(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(action=AuditAction.SYSTEM_STARTED, actor="system")
    log.record(action=AuditAction.KILL_SWITCH_ENGAGED, actor="jlin", target="kill_switch")

    entries = log.recent()
    assert [entry["action"] for entry in entries] == [
        "kill_switch.engaged",
        "system.started",
    ]


def test_filters_by_action(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(action=AuditAction.CONFIG_PATCH)
    log.record(action=AuditAction.KILL_SWITCH_ENGAGED)
    assert len(log.recent(action="config.patch")) == 1


def test_history_survives_a_restart(tmp_path: Path):
    """Un registro permanente que se olvida al reiniciar no es permanente."""
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    first.record(action=AuditAction.LIVE_ENABLE_REJECTED, actor="jlin")
    first.record(action=AuditAction.SAFE_MODE_ENTERED, actor="system")

    second = AuditLog(path)
    actions = {entry["action"] for entry in second.recent()}
    assert actions == {"live.enable_rejected", "safe_mode.entered"}


def test_corrupt_line_does_not_lose_the_rest(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record(action=AuditAction.SYSTEM_STARTED)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ no es json\n")
    AuditLog(path).record(action=AuditAction.SYSTEM_STOPPED)

    actions = {entry["action"] for entry in AuditLog(path).recent()}
    assert {"system.started", "system.stopped"} <= actions


def test_there_is_no_delete_method():
    """La regla de la fase: nunca se elimina una auditoría."""
    for forbidden in ("delete", "clear", "purge", "truncate", "remove"):
        assert not hasattr(AuditLog, forbidden)

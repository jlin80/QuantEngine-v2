"""Update Manager (Fase 9): comprueba versiones, prepara migraciones, rollback.

Nunca despliega a producción por su cuenta —eso exige aprobación humana y sale
del proceso—. Aquí se compara la versión actual con un manifiesto, se listan las
migraciones de esquema disponibles y se registra en auditoría cualquier
aplicación o rollback. ``auto_apply`` nace en ``False`` y no debe activarse en
producción.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import __version__
from app.config.settings import UpdateSettings
from app.production.audit import AuditAction, AuditLog

_log = logging.getLogger("app.production.updates")


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """Result of an update check."""

    current: str
    latest: str
    available: bool
    channel: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "current": self.current,
            "latest": self.latest,
            "available": self.available,
            "channel": self.channel,
            "notes": self.notes,
        }


class UpdateManager:
    """Check for updates, enumerate migrations and audit applies/rollbacks.

    Args:
        settings: Update configuration.
        migrations_dir: Alembic ``versions`` directory (for migration awareness).
        audit: Audit log.
    """

    def __init__(
        self,
        settings: UpdateSettings,
        migrations_dir: Path | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._settings = settings
        self._migrations_dir = migrations_dir
        self._audit = audit

    def check_for_updates(self) -> UpdateInfo:
        """Compare the running version to the configured manifest.

        Returns:
            The update info (``available`` false when up to date or no manifest).
        """
        latest = __version__
        notes = ""
        manifest = self._settings.manifest_path
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                latest = str(data.get("latest_version", __version__))
                notes = str(data.get("notes", ""))
            except (OSError, ValueError) as exc:
                _log.warning("Unreadable update manifest: %r", exc)
        available = _is_newer(latest, __version__)
        info = UpdateInfo(
            current=__version__,
            latest=latest,
            available=available,
            channel=self._settings.channel,
            notes=notes,
        )
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.UPDATE_CHECKED,
                actor="system",
                target="updates",
                after=info.to_dict(),
            )
        return info

    def pending_migrations(self) -> list[str]:
        """List Alembic migration scripts present (schema-change awareness)."""
        if self._migrations_dir is None or not self._migrations_dir.exists():
            return []
        return sorted(p.name for p in self._migrations_dir.glob("*.py") if p.stem != "__init__")

    def apply(self, *, actor: str, target_version: str) -> dict[str, Any]:
        """Record the intent to apply an update (never runs a deploy).

        Args:
            actor: Who authorizes the update.
            target_version: Version being applied.

        Returns:
            The audited record.

        Raises:
            PermissionError: If auto-apply is off and no actor authorizes it.
        """
        if not actor.strip():
            raise PermissionError("Aplicar una actualización exige un actor identificable")
        record = {"target_version": target_version, "from_version": __version__}
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.UPDATE_APPLIED,
                actor=actor,
                target="updates",
                after=record,
            )
        _log.warning("Update applied by %s: %s → %s", actor, __version__, target_version)
        return record

    def rollback(self, *, actor: str, to_version: str) -> dict[str, Any]:
        """Record a rollback to a previous version."""
        if not actor.strip():
            raise PermissionError("Un rollback exige un actor identificable")
        record = {"to_version": to_version, "from_version": __version__}
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.UPDATE_ROLLED_BACK,
                actor=actor,
                target="updates",
                after=record,
            )
        _log.warning("Rollback by %s: %s → %s", actor, __version__, to_version)
        return record

    def status(self) -> dict[str, Any]:
        """Compact update status for the dashboard."""
        return {
            "current": __version__,
            "channel": self._settings.channel,
            "auto_apply": self._settings.auto_apply,
            "migrations": len(self.pending_migrations()),
        }


def _is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a newer semantic version than ``current``."""
    try:
        return _parse(candidate) > _parse(current)
    except ValueError:
        return candidate != current


def _parse(version: str) -> tuple[int, ...]:
    """Parse a dotted version into a comparable tuple (non-numeric → 0)."""
    parts = version.strip().lstrip("v").split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)

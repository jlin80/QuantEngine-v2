"""Maintenance Manager (Fase 9): ventana de mantenimiento y limpieza.

Permite entrar/salir de una ventana de mantenimiento (que el operador puede usar
como señal para degradar) y ejecuta limpiezas periódicas de temporales/spills
antiguos. Toda entrada y salida queda auditada. La limpieza es conservadora:
sólo borra ficheros más viejos que ``temp_max_age_hours`` en los directorios
declarados, nunca fuera de ellos.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from app.config.settings import MaintenanceSettings
from app.production.audit import AuditAction, AuditLog

_log = logging.getLogger("app.production.maintenance")


class MaintenanceManager:
    """Maintenance window state + periodic cleanup of stale temp files.

    Args:
        settings: Maintenance configuration.
        audit: Audit log.
    """

    def __init__(self, settings: MaintenanceSettings, audit: AuditLog | None = None) -> None:
        self._settings = settings
        self._audit = audit
        self._active = False
        self._reason = ""

    @property
    def active(self) -> bool:
        """Whether a maintenance window is currently open."""
        return self._active

    def enter(self, *, actor: str, reason: str) -> dict[str, Any]:
        """Open a maintenance window (audited)."""
        self._active = True
        self._reason = reason
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.MAINTENANCE_STARTED,
                actor=actor,
                target="maintenance",
                meta={"reason": reason},
            )
        _log.warning("Maintenance window opened by %s: %s", actor, reason)
        return self.status()

    def exit(self, *, actor: str) -> dict[str, Any]:
        """Close the maintenance window (audited)."""
        self._active = False
        reason, self._reason = self._reason, ""
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.MAINTENANCE_FINISHED,
                actor=actor,
                target="maintenance",
                meta={"reason": reason},
            )
        _log.info("Maintenance window closed by %s", actor)
        return self.status()

    def cleanup(self) -> dict[str, Any]:
        """Delete stale temp files older than the configured max age (blocking).

        Returns:
            ``{"removed": int, "bytes_freed": int}``.
        """
        max_age = self._settings.temp_max_age_hours * 3600.0
        cutoff = time.time() - max_age
        removed = 0
        bytes_freed = 0
        for directory in self._settings.cleanup_dirs:
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        size = path.stat().st_size
                        path.unlink(missing_ok=True)
                        removed += 1
                        bytes_freed += size
                except OSError as exc:
                    _log.warning("Could not remove stale file %s: %r", path, exc)
        _log.info("Maintenance cleanup removed %d files (%d bytes)", removed, bytes_freed)
        return {"removed": removed, "bytes_freed": bytes_freed}

    async def acleanup(self) -> dict[str, Any]:
        """Async wrapper around :meth:`cleanup` (runs in a worker thread)."""
        return await asyncio.to_thread(self.cleanup)

    def status(self) -> dict[str, Any]:
        """Compact maintenance status for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "active": self._active,
            "reason": self._reason,
            "cleanup_dirs": [str(Path(d)) for d in self._settings.cleanup_dirs],
        }

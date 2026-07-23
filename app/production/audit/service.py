"""Append-only audit log — la memoria permanente del sistema.

Nació en la Fase 8 para las escrituras del dashboard; la Fase 9 lo promueve a
servicio de todo el motor (arranques, kill switch, safe mode, live gating,
backups, actualizaciones). Sigue siendo un JSON Lines append-only más un anillo
en memoria para lecturas rápidas, al estilo del Trade Journal — sin migración
de base de datos.

Regla: **nunca se elimina una auditoría.** No hay método de borrado, y no debe
añadirse.
"""

import json
import logging
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any

from app.production.audit.taxonomy import AuditAction
from app.utils.time import utc_now

_log = logging.getLogger("app.production.audit")


class AuditLog:
    """Thread-safe append-only audit trail (JSONL file + in-memory ring).

    Args:
        path: Destination JSONL file, or ``None`` to keep entries in memory only.
        ring_size: Maximum number of recent entries kept in memory.
    """

    def __init__(self, path: Path | None = None, ring_size: int = 500) -> None:
        self._path = path
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._lock = Lock()
        self._load_tail()

    def _load_tail(self) -> None:
        """Seed the ring with the tail of the existing JSONL.

        Sin esto, tras un reinicio ``recent()`` sólo devolvía lo ocurrido desde
        el arranque aunque el fichero tuviera el histórico completo: el
        dashboard mostraría una auditoría amnésica de un registro que sí lo
        recuerda todo.
        """
        if self._path is None or not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._ring.append(json.loads(line))
                    except ValueError:
                        continue  # una línea corrupta no invalida el resto
        except OSError as exc:
            _log.warning("Audit tail load failed: %r", exc)

    def record(
        self,
        *,
        action: AuditAction | str,
        actor: str = "dashboard",
        target: str | None = None,
        before: Any = None,
        after: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one audited action.

        Args:
            action: Action name, preferably an :class:`AuditAction` member.
            actor: Who performed it (operator, ``system``, ``dashboard``...).
            target: Optional subject of the action (e.g. a strategy name).
            before: Optional previous state.
            after: Optional resulting state.
            meta: Optional extra context.

        Returns:
            The stored entry.
        """
        entry: dict[str, Any] = {
            "timestamp": utc_now().isoformat(),
            "action": str(action),
            "actor": actor,
            "target": target,
            "before": before,
            "after": after,
            "meta": meta or {},
        }
        with self._lock:
            self._ring.append(entry)
            if self._path is not None:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with self._path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                except OSError as exc:
                    _log.warning("Audit persist failed: %r", exc)
        return entry

    def recent(self, limit: int = 100, *, action: str | None = None) -> list[dict[str, Any]]:
        """Return the most recent entries, newest first.

        Args:
            limit: Maximum number of entries to return.
            action: Optional exact action filter.

        Returns:
            Recent audit entries.
        """
        with self._lock:
            items = list(self._ring)
        ordered = list(reversed(items))
        if action is not None:
            ordered = [entry for entry in ordered if entry.get("action") == action]
        return ordered[:limit]

    def count(self) -> int:
        """Number of entries currently held in memory."""
        with self._lock:
            return len(self._ring)


audit_log = AuditLog(Path("logs") / "audit.jsonl")
"""Process-wide audit log singleton."""

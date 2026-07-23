"""Failover por arriendo de líder sobre un fichero compartido (Fase 9).

Alta disponibilidad mínima y sin infraestructura externa: varios nodos apuntan
al mismo fichero de arriendo (en un volumen/NFS compartido). Sólo el nodo que
sostiene un arriendo **fresco** es el primario y debe operar; un standby toma el
relevo cuando el arriendo caduca (el primario murió sin renovarlo).

La regla de la fase manda: ante la duda, no operar. Si el fichero de arriendo no
se puede leer/escribir, este nodo se considera standby (no primario), nunca al
revés — dos primarios operando a la vez es el peor resultado posible.
"""

import json
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any

from app.config.settings import FailoverSettings
from app.production.audit import AuditAction, AuditLog
from app.utils.time import utc_now

_log = logging.getLogger("app.production.failover")


@dataclass(frozen=True, slots=True)
class _Lease:
    """A leader lease read from (or written to) the shared file."""

    node_id: str
    updated_at: float  # epoch seconds

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {"node_id": self.node_id, "updated_at": self.updated_at}


class FailoverCoordinator:
    """File-lease based leader election for primary/standby high availability.

    Args:
        settings: Failover configuration.
        audit: Audit log (promotions/demotions are recorded).
    """

    def __init__(self, settings: FailoverSettings, audit: AuditLog | None = None) -> None:
        self._settings = settings
        self._audit = audit
        self._node_id = settings.node_id.strip() or f"{socket.gethostname()}:{os.getpid()}"
        self._is_primary = not settings.enabled  # single-node → siempre primario

    @property
    def node_id(self) -> str:
        """This node's identifier."""
        return self._node_id

    @property
    def is_primary(self) -> bool:
        """Whether this node currently holds leadership."""
        return self._is_primary

    def should_run(self) -> bool:
        """Whether this node should be operating (primary, or HA disabled)."""
        return self._is_primary

    def heartbeat(self, *, now: float | None = None) -> bool:
        """Acquire or renew the lease; return whether this node is primary.

        Args:
            now: Reference epoch seconds (defaults to wall clock).

        Returns:
            Whether this node is the primary after the heartbeat.
        """
        if not self._settings.enabled:
            return True
        moment = now if now is not None else utc_now().timestamp()
        lease = self._read_lease()
        was_primary = self._is_primary

        held_by_me = lease is not None and lease.node_id == self._node_id
        expired = lease is None or (moment - lease.updated_at) > self._settings.lease_ttl_seconds

        if held_by_me or expired:
            # Puedo renovar (es mío) o tomarlo (caducó). Ante fallo de escritura,
            # _write_lease deja _is_primary en False: nunca asumir liderazgo.
            self._write_lease(_Lease(self._node_id, moment))
        else:
            self._set_primary(False)

        if self._is_primary != was_primary:
            self._audit_transition()
        return self._is_primary

    def release(self) -> None:
        """Release leadership so a standby can take over promptly."""
        if not self._settings.enabled:
            return
        lease = self._read_lease()
        if lease is not None and lease.node_id == self._node_id:
            try:
                self._settings.lease_path.unlink(missing_ok=True)
            except OSError as exc:
                _log.warning("Could not release lease: %r", exc)
        was_primary = self._is_primary
        self._is_primary = False
        if was_primary:
            self._audit_transition()

    def status(self) -> dict[str, Any]:
        """Compact failover status for the dashboard."""
        lease = self._read_lease()
        return {
            "enabled": self._settings.enabled,
            "node_id": self._node_id,
            "is_primary": self._is_primary,
            "current_lease": lease.to_dict() if lease is not None else None,
            "lease_ttl_seconds": self._settings.lease_ttl_seconds,
        }

    # ------------------------------------------------------------------
    # Persistencia del arriendo
    # ------------------------------------------------------------------

    def _read_lease(self) -> _Lease | None:
        """Read the current lease (best effort)."""
        path = self._settings.lease_path
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _Lease(node_id=str(data["node_id"]), updated_at=float(data["updated_at"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _log.warning("Unreadable failover lease: %r", exc)
            return None

    def _write_lease(self, lease: _Lease) -> None:
        """Write the lease; only claim primary if the write succeeds."""
        path = self._settings.lease_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(lease.to_dict()), encoding="utf-8")
            self._set_primary(True)
        except OSError as exc:
            _log.error("Could not write failover lease (staying standby): %r", exc)
            self._set_primary(False)

    def _set_primary(self, value: bool) -> None:
        """Update the primary flag, logging real transitions."""
        if value != self._is_primary:
            _log.warning("Failover: node %s %s", self._node_id, "PROMOTED" if value else "DEMOTED")
        self._is_primary = value

    def _audit_transition(self) -> None:
        """Record a leadership transition in the audit trail."""
        if self._audit is None:
            return
        self._audit.record(
            action=(
                AuditAction.FAILOVER_PROMOTED if self._is_primary else AuditAction.FAILOVER_DEMOTED
            ),
            actor="system",
            target=self._node_id,
            after={"is_primary": self._is_primary},
        )

"""Aprobación explícita del operador para habilitar Live Trading.

El spec es tajante: hace falta "aprobación explícita del operador desde el
Dashboard" y no puede haber atajos. Esta aprobación no es un booleano suelto —
va firmada contra el *hash* del reporte del Live Gate que el operador vio.

Consecuencia deliberada: si después de aprobar cambia cualquier criterio del
gate (bajar un umbral, por ejemplo), el hash deja de coincidir y la aprobación
queda invalidada automáticamente. Aprobar el sistema de ayer no aprueba el de
hoy.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from app.utils.time import utc_now

_log = logging.getLogger("app.production.live.approval")


@dataclass(frozen=True, slots=True)
class LiveApproval:
    """One operator approval, bound to the gate report it was granted against.

    Attributes:
        actor: Who approved (never empty).
        reason: Why they approved.
        report_hash: Hash of the gate report shown to the operator.
        granted_at: UTC moment of approval.
        ttl_hours: How long the approval stays valid.
    """

    actor: str
    reason: str
    report_hash: str
    granted_at: datetime
    ttl_hours: float

    @property
    def expires_at(self) -> datetime:
        """Moment the approval stops being valid."""
        return self.granted_at + timedelta(hours=self.ttl_hours)

    def is_valid_for(self, report_hash: str, *, now: datetime | None = None) -> bool:
        """Whether this approval authorizes the given gate report.

        Args:
            report_hash: Hash of the report being authorized.
            now: Reference moment (defaults to now).

        Returns:
            ``True`` only if the hash matches and the approval has not expired.
        """
        moment = now or utc_now()
        return report_hash == self.report_hash and moment < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "actor": self.actor,
            "reason": self.reason,
            "report_hash": self.report_hash,
            "granted_at": self.granted_at.isoformat(),
            "ttl_hours": self.ttl_hours,
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LiveApproval":
        """Rebuild an approval from its persisted form.

        Args:
            data: Mapping produced by :meth:`to_dict`.

        Returns:
            The reconstructed approval.

        Raises:
            ValueError: If a required field is missing or malformed.
        """
        return cls(
            actor=str(data["actor"]),
            reason=str(data.get("reason", "")),
            report_hash=str(data["report_hash"]),
            granted_at=datetime.fromisoformat(str(data["granted_at"])),
            ttl_hours=float(data["ttl_hours"]),
        )


class ApprovalStore:
    """Persisted store of the current operator approval (at most one).

    Args:
        path: JSON file backing the store, or ``None`` for in-memory only.
        ttl_hours: Validity window granted to new approvals.
    """

    def __init__(self, path: Path | None = None, ttl_hours: float = 24.0) -> None:
        self._path = path
        self._ttl_hours = ttl_hours
        self._approval: LiveApproval | None = None
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        """Load the stored approval (best effort — a bad file means no approval)."""
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._approval = LiveApproval.from_dict(data)
        except (OSError, ValueError, KeyError) as exc:
            # Fail closed: si no podemos leer la aprobación, no hay aprobación.
            _log.warning("Live approval load failed, treating as unapproved: %r", exc)
            self._approval = None

    def _persist(self) -> None:
        """Persist the current approval (best effort)."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._approval is None:
                self._path.unlink(missing_ok=True)
                return
            self._path.write_text(json.dumps(self._approval.to_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            _log.warning("Live approval persist failed: %r", exc)

    @property
    def current(self) -> LiveApproval | None:
        """The stored approval, if any (may be expired or stale)."""
        with self._lock:
            return self._approval

    def grant(self, *, actor: str, reason: str, report_hash: str) -> LiveApproval:
        """Record an operator approval for a specific gate report.

        Args:
            actor: Who approves. Must be non-empty — an anonymous approval is
                not an approval.
            reason: Why they approve.
            report_hash: Hash of the gate report they reviewed.

        Returns:
            The stored approval.

        Raises:
            ValueError: If ``actor`` or ``report_hash`` is empty.
        """
        if not actor.strip():
            raise ValueError("La aprobación de live exige un actor identificable")
        if not report_hash.strip():
            raise ValueError("La aprobación de live exige el hash del reporte del gate")
        approval = LiveApproval(
            actor=actor.strip(),
            reason=reason.strip(),
            report_hash=report_hash,
            granted_at=utc_now(),
            ttl_hours=self._ttl_hours,
        )
        with self._lock:
            self._approval = approval
            self._persist()
        _log.warning("Live trading approved by %s (hash %s)", approval.actor, report_hash[:12])
        return approval

    def revoke(self) -> None:
        """Drop the current approval (live falls back to paper immediately)."""
        with self._lock:
            self._approval = None
            self._persist()

    def is_valid_for(self, report_hash: str) -> bool:
        """Whether a valid, unexpired approval exists for this exact report.

        Args:
            report_hash: Hash of the gate report being authorized.

        Returns:
            ``True`` only if an approval matches and has not expired.
        """
        with self._lock:
            approval = self._approval
        return approval is not None and approval.is_valid_for(report_hash)

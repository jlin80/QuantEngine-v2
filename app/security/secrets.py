"""Vigilancia de rotación de secretos (Fase 9).

No rota secretos por su cuenta —eso vive en el gestor de secretos de la
infraestructura— pero **vigila su antigüedad** y avisa cuando toca rotarlos.
Nunca lee ni imprime el valor del secreto: sólo registra *cuándo* se rotó por
última vez, en un fichero de estado con marcas de tiempo. Un secreto sin marca
se considera vencido (fail-closed), para que aparezca en la lista de pendientes.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.settings import SecuritySettings
from app.production.audit import AuditAction, AuditLog
from app.utils.time import utc_now

_log = logging.getLogger("app.security")


@dataclass(frozen=True, slots=True)
class SecretStatus:
    """Rotation status of one tracked secret."""

    name: str
    age_days: float | None
    due: bool

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {"name": self.name, "age_days": self.age_days, "due": self.due}


class SecretRotationManager:
    """Track secret ages and flag the ones due for rotation.

    Args:
        settings: Security configuration (max age).
        state_path: JSON file recording last-rotation timestamps per secret.
        audit: Audit log.
    """

    def __init__(
        self,
        settings: SecuritySettings,
        state_path: Path,
        audit: AuditLog | None = None,
    ) -> None:
        self._settings = settings
        self._state_path = state_path
        self._audit = audit

    def mark_rotated(self, name: str) -> None:
        """Record that a secret was just rotated (call after rotating it)."""
        state = self._load()
        state[name] = utc_now().isoformat()
        self._save(state)
        _log.info("Secret rotation recorded for %r", name)

    def check(self, names: list[str]) -> list[SecretStatus]:
        """Return the rotation status of each tracked secret name.

        Args:
            names: Logical secret names to evaluate (e.g. ``broker.api_key``).

        Returns:
            Per-secret status; unknown secrets are reported as due (fail-closed).
        """
        from datetime import datetime  # sólo aquí se parsean las marcas

        state = self._load()
        now = utc_now()
        max_age = self._settings.secret_max_age_days
        statuses: list[SecretStatus] = []
        for name in names:
            raw = state.get(name)
            if not raw:
                statuses.append(SecretStatus(name=name, age_days=None, due=True))
                continue
            try:
                rotated_at = datetime.fromisoformat(raw)
            except ValueError:
                statuses.append(SecretStatus(name=name, age_days=None, due=True))
                continue
            age_days = (now - rotated_at).total_seconds() / 86_400.0
            statuses.append(
                SecretStatus(name=name, age_days=round(age_days, 2), due=age_days >= max_age)
            )

        due = [s for s in statuses if s.due]
        if due and self._audit is not None:
            self._audit.record(
                action=AuditAction.SECRET_ROTATION_DUE,
                actor="system",
                target="secrets",
                after={"due": [s.name for s in due], "max_age_days": max_age},
            )
        return statuses

    def status(self, names: list[str]) -> dict[str, Any]:
        """Compact rotation status for the dashboard."""
        statuses = self.check(names)
        return {
            "max_age_days": self._settings.secret_max_age_days,
            "due": [s.name for s in statuses if s.due],
            "secrets": [s.to_dict() for s in statuses],
        }

    def _load(self) -> dict[str, str]:
        """Read the rotation-state file (best effort)."""
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError) as exc:
            _log.warning("Unreadable secret-rotation state: %r", exc)
            return {}

    def _save(self, state: dict[str, str]) -> None:
        """Write the rotation-state file (best effort)."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError as exc:
            _log.warning("Could not persist secret-rotation state: %r", exc)

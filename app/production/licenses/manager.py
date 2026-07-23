"""License Manager (Fase 9): estructura preparada, sin restricciones activas.

La spec es explícita: **preparar la estructura para futuras licencias, sin
implementar restricciones todavía**. Por eso ``is_feature_enabled`` devuelve
siempre ``True`` y ``status`` reporta el plan como ``unrestricted``. Cuando en
una fase futura se decida licenciar, la lógica de validación vive aquí y el resto
del sistema ya consulta esta fachada.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.config.settings import LicenseSettings
from app.production.audit import AuditAction, AuditLog

_log = logging.getLogger("app.production.licenses")


@dataclass(frozen=True, slots=True)
class LicenseStatus:
    """Current licensing state."""

    enabled: bool
    valid: bool
    plan: str
    holder: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "enabled": self.enabled,
            "valid": self.valid,
            "plan": self.plan,
            "holder": self.holder,
        }


class LicenseManager:
    """Prepared licensing facade — enforces nothing yet.

    Args:
        settings: License configuration.
        audit: Audit log.
    """

    def __init__(self, settings: LicenseSettings, audit: AuditLog | None = None) -> None:
        self._settings = settings
        self._audit = audit

    def validate(self) -> LicenseStatus:
        """Validate the license (no-op while licensing is disabled).

        Returns:
            The licensing status. With ``enabled`` false the plan is
            ``unrestricted`` and everything is valid.
        """
        if not self._settings.enabled:
            status = LicenseStatus(enabled=False, valid=True, plan="unrestricted")
        else:
            status = self._load_license()
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.LICENSE_VALIDATED,
                actor="system",
                target="license",
                after=status.to_dict(),
            )
        return status

    def _load_license(self) -> LicenseStatus:
        """Load and validate a license file (structure for future use)."""
        path = self._settings.license_path
        if not path.exists():
            return LicenseStatus(enabled=True, valid=False, plan="unlicensed")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return LicenseStatus(
                enabled=True,
                valid=bool(data.get("valid", True)),
                plan=str(data.get("plan", "custom")),
                holder=str(data.get("holder", "")),
            )
        except (OSError, ValueError) as exc:
            _log.warning("Unreadable license file: %r", exc)
            return LicenseStatus(enabled=True, valid=False, plan="invalid")

    def is_feature_enabled(self, feature: str) -> bool:
        """Whether a feature is licensed.

        Siempre ``True``: no hay restricciones todavía (por diseño). El parámetro
        se conserva para que los llamadores ya escriban la comprobación.
        """
        _ = feature
        return True

    def status(self) -> dict[str, Any]:
        """Compact license status for the dashboard."""
        return (
            self.validate().to_dict()
            if self._settings.enabled
            else {
                "enabled": False,
                "valid": True,
                "plan": "unrestricted",
                "holder": "",
            }
        )

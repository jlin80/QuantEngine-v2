"""Resolución autorizada del modo de ejecución.

Hasta la Fase 8, ``ExecutionSettings.resolved_mode()`` era literalmente
``return "paper"``: no existía fontanería de modo, sólo una constante. Este
módulo la construye de verdad, y la construye cerrada.

``ModeResolver`` es lo único en todo el sistema autorizado a decir ``"live"``, y
sólo lo dice cuando se cumplen **todas** estas condiciones a la vez:

1. ``production.allow_live`` está en ``True`` (llave maestra, nace cerrada).
2. ``execution.mode`` pide ``live`` explícitamente.
3. El ambiente es ``production``.
4. El Live Gate aprobó, con una evaluación reciente y no caduca.
5. Esa evaluación cuenta con la aprobación explícita del operador.

Cualquier otro caso —incluido "no lo he evaluado todavía"— resuelve ``paper``.
"""

import logging
from typing import Any

from app.config.environment import Environment
from app.config.settings import Settings
from app.production.live.gate import LiveGate, LiveGateReport
from app.utils.time import utc_now

_log = logging.getLogger("app.production.live.mode")

PAPER = "paper"
LIVE = "live"


class ModeResolver:
    """Resolve the effective execution mode from configuration + live state.

    Args:
        settings: Root settings.
        gate: Live Gate holding the latest evaluation.
        max_report_age_seconds: How old a gate report may be and still count.
            A stale approval is not an approval.
    """

    def __init__(
        self,
        settings: Settings,
        gate: LiveGate,
        *,
        max_report_age_seconds: float = 300.0,
    ) -> None:
        self._settings = settings
        self._gate = gate
        self._max_age = max_report_age_seconds

    def resolved_mode(self) -> str:
        """Return the effective mode — ``paper`` unless every gate passes.

        Returns:
            ``"live"`` only when the full chain authorizes it, else ``"paper"``.
        """
        return LIVE if self._live_authorized() is None else PAPER

    def blocking_reason(self) -> str:
        """Explain why live is not active (empty string when it is).

        Returns:
            The first blocking reason, or ``""`` if live is authorized.
        """
        return self._live_authorized() or ""

    def _live_authorized(self) -> str | None:
        """Return the first blocking reason, or ``None`` when live is allowed."""
        production = self._settings.production
        if not production.allow_live:
            return "production.allow_live está en False (llave maestra cerrada)"
        if not self._settings.execution.is_live:
            return "execution.mode no pide live"
        if self._settings.environment is not Environment.PRODUCTION:
            return f"ambiente {self._settings.environment.value!r}, live exige 'production'"
        report = self._gate.last_report
        if report is None:
            return "el Live Gate no se ha evaluado todavía"
        age = (utc_now() - report.evaluated_at).total_seconds()
        if age > self._max_age:
            return f"la evaluación del Live Gate tiene {age:.0f}s (máximo {self._max_age:.0f}s)"
        if not report.approved:
            return f"el Live Gate no aprueba: {'; '.join(report.reasons[:3])}"
        return None

    def status(self) -> dict[str, Any]:
        """Full mode status for the dashboard and the audit trail."""
        report: LiveGateReport | None = self._gate.last_report
        reason = self._live_authorized()
        return {
            "mode": PAPER if reason is not None else LIVE,
            "requested_mode": self._settings.execution.mode,
            "live_enabled": reason is None,
            "allow_live": self._settings.production.allow_live,
            "environment": self._settings.environment.value,
            "blocking_reason": reason or "",
            "gate": report.to_dict() if report is not None else None,
        }

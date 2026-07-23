"""ReportService: entrega los reportes operativos automáticos (Fase 9).

Arma el reporte con :func:`build_report` y lo publica en el canal lógico
``reportes`` de Discord, dejando además constancia en la auditoría. No decide
nada ni toca la operación: sólo observa y comunica.
"""

import logging

from app.config.settings import ReportingSettings
from app.core.container import Container
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService
from app.production.audit import AuditAction, AuditLog
from app.production.reporting.builder import OperationalReport, build_report

_log = logging.getLogger("app.production.reporting")


class ReportService:
    """Build and deliver periodic operational reports.

    Args:
        settings: Reporting configuration.
        container: DI container used to observe the live system.
        notifications: Notification service (Discord).
        audit: Audit log (every report is recorded).
    """

    def __init__(
        self,
        settings: ReportingSettings,
        container: Container,
        notifications: NotificationService,
        audit: AuditLog | None = None,
    ) -> None:
        self._settings = settings
        self._container = container
        self._notifications = notifications
        self._audit = audit

    def build(self) -> OperationalReport:
        """Assemble the current operational report."""
        return build_report(self._container)

    async def send_hourly(self) -> OperationalReport:
        """Send the hourly operational summary."""
        return await self._send("📊 Resumen operativo (última hora)", "hourly")

    async def send_daily(self) -> OperationalReport:
        """Send the full daily report."""
        return await self._send("🗓️ Reporte diario", "daily")

    async def _send(self, title: str, period: str) -> OperationalReport:
        """Build, deliver and audit a report."""
        report = self.build()
        if self._settings.enabled:
            level = (
                NotificationLevel.WARNING
                if report.health_status not in ("healthy", "unknown")
                else NotificationLevel.INFO
            )
            await self._notifications.send(
                title,
                f"Estado del sistema: **{report.health_status}**.",
                level=level,
                fields=report.discord_fields(),
                source="reporting",
                channel=self._settings.channel,
            )
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.REPORT_SENT,
                actor="system",
                target=period,
                after=report.to_dict(),
            )
        _log.info("Operational report sent (%s)", period)
        return report

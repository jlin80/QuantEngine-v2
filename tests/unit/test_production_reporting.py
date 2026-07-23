"""Reportes operativos: construcción degradable y entrega al canal 'reportes'."""

from app.config.settings import ReportingSettings
from app.core.container import Container
from app.notifications.models import Notification
from app.notifications.service import NotificationService
from app.production.audit import AuditAction, AuditLog
from app.production.reporting import ReportService, build_report


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    @property
    def channel_name(self) -> str:
        return "discord"

    async def send(self, notification: Notification) -> None:
        self.sent.append(notification)

    async def close(self) -> None:
        pass


def test_build_report_degrades_without_wiring():
    report = build_report(None)
    assert report.health_status == "unknown"
    assert report.balance is None
    # Nunca inventa métricas: los campos no observables quedan vacíos.
    assert report.to_dict()["trading"]["pnl"] is None


async def test_report_service_sends_to_reports_channel(tmp_path):
    container = Container()
    recorder = _Recorder()
    notifications = NotificationService()
    notifications.register_channel(recorder)
    await notifications.start()
    container.register_instance(NotificationService, notifications)

    audit = AuditLog(tmp_path / "audit.jsonl")
    service = ReportService(ReportingSettings(), container, notifications, audit)

    report = await service.send_daily()

    assert len(recorder.sent) == 1
    assert recorder.sent[0].channel == "reportes"
    assert recorder.sent[0].source == "reporting"
    assert report.discord_delivered >= 0
    # Todo reporte queda auditado.
    assert audit.recent(action=str(AuditAction.REPORT_SENT))
    await notifications.stop()


async def test_reporting_disabled_still_audits_but_sends_nothing(tmp_path):
    container = Container()
    recorder = _Recorder()
    notifications = NotificationService()
    notifications.register_channel(recorder)
    await notifications.start()
    container.register_instance(NotificationService, notifications)

    audit = AuditLog(tmp_path / "audit.jsonl")
    service = ReportService(ReportingSettings(enabled=False), container, notifications, audit)
    await service.send_hourly()
    assert recorder.sent == []
    assert audit.recent(action=str(AuditAction.REPORT_SENT))
    await notifications.stop()

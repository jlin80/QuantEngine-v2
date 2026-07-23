"""ProductionAPI: fachada de las APIs internas de la capa de producción.

Punto único de entrada para el dashboard y los scripts, al estilo de
``QuantCore``, ``ExecutionCore``, ``BacktestLab`` y ``MLEngine``. Expone las
operaciones que pide la especificación de la Fase 9 —``enable_live_trading``,
``disable_live_trading``, ``activate_safe_mode``, ``kill_switch``,
``health_report``, ``audit_event``, ``system_status``— sobre los controladores
desacoplados de este paquete.

**Ninguna de estas operaciones puede saltarse el Live Gate.**
``enable_live_trading`` no es un interruptor: evalúa, y si algo falta devuelve
el reporte explicando qué falta. Lo único que "enciende" es la aprobación del
operador, que sigue siendo un criterio más entre veinte.
"""

import logging
from collections.abc import Sequence
from typing import Any

from app.config.settings import Settings
from app.core.events.bus import EventBus
from app.documentation.service import DocumentationService
from app.execution.api import ExecutionCore
from app.execution.models import TradeRecord
from app.monitoring.health import HealthMonitor
from app.production.audit import AuditAction, AuditLog
from app.production.backup import BackupService
from app.production.events import LiveTradingDisabled, LiveTradingEnabled
from app.production.failover import FailoverCoordinator
from app.production.improvement import ImprovementService
from app.production.kill_switch import KillSwitchController, KillSwitchTrigger
from app.production.licenses import LicenseManager
from app.production.live import ApprovalStore, GateInputs, LiveGate, ModeResolver
from app.production.maintenance import MaintenanceManager
from app.production.recovery import RecoveryService
from app.production.reporting import ReportService
from app.production.safe_mode import SafeModeController, SafeModeObservation
from app.production.updates import UpdateManager
from app.security.config_validator import validation_report
from app.security.secrets import SecretRotationManager

_log = logging.getLogger("app.production.api")

#: Secretos que la vigilancia de rotación evalúa (nombres lógicos, sin valores).
_TRACKED_SECRETS = ("broker.api_key", "broker.api_secret", "discord.webhook_url", "notion.api_key")


class ProductionAPI:
    """Facade over the production layer.

    Args:
        settings: Root settings.
        gate: Live Gate.
        approvals: Operator approval store.
        mode: Mode resolver.
        safe_mode: Safe Mode controller.
        kill_switch: Kill switch controller.
        recovery: Recovery service.
        audit: Audit log.
        execution: Execution layer facade (``None`` when disabled).
        health: Health monitor (``None`` when unavailable).
        bus: Event Bus.
        backup: Backup service (``None`` when disabled).
        updates: Update manager (``None`` when disabled).
        licenses: License manager (``None`` when disabled).
        maintenance: Maintenance manager (``None`` when disabled).
        failover: Failover coordinator (``None`` when disabled).
        reporting: Operational report service (``None`` when disabled).
        improvement: Continuous improvement service (``None`` when disabled).
        documentation: Documentation service (for Notion sync).
        secrets: Secret rotation manager (``None`` when disabled).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        gate: LiveGate,
        approvals: ApprovalStore,
        mode: ModeResolver,
        safe_mode: SafeModeController,
        kill_switch: KillSwitchController,
        recovery: RecoveryService,
        audit: AuditLog,
        execution: ExecutionCore | None = None,
        health: HealthMonitor | None = None,
        bus: EventBus | None = None,
        backup: BackupService | None = None,
        updates: UpdateManager | None = None,
        licenses: LicenseManager | None = None,
        maintenance: MaintenanceManager | None = None,
        failover: FailoverCoordinator | None = None,
        reporting: ReportService | None = None,
        improvement: ImprovementService | None = None,
        documentation: DocumentationService | None = None,
        secrets: SecretRotationManager | None = None,
    ) -> None:
        self._settings = settings
        self.gate = gate
        self.approvals = approvals
        self.mode = mode
        self.safe_mode = safe_mode
        self.kill_switch = kill_switch
        self.recovery = recovery
        self.audit = audit
        self.execution = execution
        self.health = health
        self._bus = bus
        self.backup = backup
        self.updates = updates
        self.licenses = licenses
        self.maintenance = maintenance
        self.failover = failover
        self.reporting = reporting
        self.improvement = improvement
        self.documentation = documentation
        self.secrets = secrets

    # ------------------------------------------------------------------
    # Live Trading
    # ------------------------------------------------------------------

    def collect_gate_inputs(self) -> GateInputs:
        """Observe the live system and build the gate's inputs.

        Lo que no se puede observar se deja en ``None``, y el gate lo cuenta
        como fallido. Es deliberado: un criterio que no sabemos medir no puede
        contar como superado.

        Returns:
            The observed inputs.
        """
        execution = self.execution
        health_snapshot = self.health.last_snapshot if self.health is not None else None
        performance: dict[str, Any] = {}
        trades: list[TradeRecord] = []
        if execution is not None:
            trades = execution.journal.all()
            performance = execution.performance.compute(trades).to_dict()
        return GateInputs(
            allow_live=self._settings.production.allow_live,
            mode_requested=self._settings.execution.mode,
            environment=self._settings.environment.value,
            paper_trades=len(trades),
            paper_days=_track_record_days(trades),
            profit_factor=_opt(performance.get("profit_factor")),
            sharpe=_opt(performance.get("sharpe")),
            max_drawdown_pct=_opt(performance.get("max_drawdown_pct")),
            system_healthy=(
                None if health_snapshot is None else health_snapshot.status == "healthy"
            ),
            broker_connected=(None if execution is None else execution.engine.broker.healthcheck()),
            discord_ok=bool(self._settings.discord.enabled),
            risk_manager_ok=execution is not None,
            kill_switch_active=self.kill_switch.active,
            safe_mode_active=self.safe_mode.active,
        )

    def evaluate_live_gate(self) -> dict[str, Any]:
        """Evaluate every live-trading criterion and explain the outcome.

        Returns:
            The report as a JSON-safe dict.
        """
        report = self.gate.evaluate(self.collect_gate_inputs())
        self.audit.record(
            action=AuditAction.LIVE_GATE_EVALUATED,
            actor="system",
            target="live_gate",
            after={"approved": report.approved, "failed": report.failed_count},
            meta={"report_hash": report.report_hash},
        )
        return report.to_dict()

    def approve_live_trading(self, *, actor: str, reason: str) -> dict[str, Any]:
        """Record the operator's explicit approval of the current gate report.

        Approving does **not** enable live trading — it satisfies one criterion.
        The approval is bound to the exact report the operator reviewed, so any
        later change to the criteria invalidates it.

        Args:
            actor: Who approves.
            reason: Why.

        Returns:
            The re-evaluated gate report after recording the approval.

        Raises:
            ValueError: If the actor is empty.
        """
        report = self.gate.evaluate(self.collect_gate_inputs())
        approval = self.approvals.grant(actor=actor, reason=reason, report_hash=report.report_hash)
        self.audit.record(
            action=AuditAction.LIVE_APPROVAL_GRANTED,
            actor=actor,
            target="live_trading",
            after=approval.to_dict(),
            meta={"reason": reason},
        )
        # Re-evaluar para que el reporte devuelto ya refleje la aprobación.
        return self.gate.evaluate(self.collect_gate_inputs()).to_dict()

    async def enable_live_trading(self, *, actor: str) -> dict[str, Any]:
        """Attempt to enable live trading. Fails loudly when anything is missing.

        Args:
            actor: Who requests it.

        Returns:
            ``{"enabled": bool, "report": ..., "blocking_reason": str}``.
        """
        report = self.gate.evaluate(self.collect_gate_inputs())
        resolved = self.mode.resolved_mode()
        if not report.approved or resolved != "live":
            reason = self.mode.blocking_reason() or "; ".join(report.reasons[:3])
            self.audit.record(
                action=AuditAction.LIVE_ENABLE_REJECTED,
                actor=actor,
                target="live_trading",
                meta={"reason": reason, "report_hash": report.report_hash},
            )
            _log.warning("Habilitación de live rechazada (%s): %s", actor, reason)
            return {
                "enabled": False,
                "blocking_reason": reason,
                "report": report.to_dict(),
            }
        self.audit.record(
            action=AuditAction.LIVE_ENABLED,
            actor=actor,
            target="live_trading",
            meta={"report_hash": report.report_hash},
        )
        if self._bus is not None:
            await self._bus.publish(
                LiveTradingEnabled(source="production", actor=actor, report_hash=report.report_hash)
            )
        return {"enabled": True, "blocking_reason": "", "report": report.to_dict()}

    async def disable_live_trading(self, *, actor: str, reason: str) -> dict[str, Any]:
        """Disable live trading by revoking the operator approval.

        Args:
            actor: Who disables it.
            reason: Why.

        Returns:
            The resulting mode status.
        """
        self.approvals.revoke()
        self.audit.record(
            action=AuditAction.LIVE_DISABLED,
            actor=actor,
            target="live_trading",
            meta={"reason": reason},
        )
        if self._bus is not None:
            await self._bus.publish(
                LiveTradingDisabled(source="production", actor=actor, reason=reason)
            )
        return self.mode.status()

    # ------------------------------------------------------------------
    # Safe Mode y Kill Switch
    # ------------------------------------------------------------------

    async def activate_safe_mode(self, *, reason: str) -> dict[str, Any]:
        """Force Safe Mode on (manual operator action).

        Args:
            reason: Why the operator is degrading the system.

        Returns:
            Safe Mode status.
        """
        await self.safe_mode.evaluate(SafeModeObservation(api_ok=False))
        _log.warning("Safe Mode forzado manualmente: %s", reason)
        return self.safe_mode.status()

    async def kill_switch_engage(
        self, *, actor: str, reason: str, trigger: KillSwitchTrigger = KillSwitchTrigger.MANUAL
    ) -> dict[str, Any]:
        """Engage the global kill switch.

        Args:
            actor: Who triggers it.
            reason: Why.
            trigger: Trigger source.

        Returns:
            Kill switch status.
        """
        await self.kill_switch.engage(trigger, reason, actor=actor)
        return self.kill_switch.status()

    async def kill_switch_release(self, *, actor: str, reason: str) -> dict[str, Any]:
        """Release the global kill switch (never anonymous, never unexplained).

        Args:
            actor: Who releases it.
            reason: Why it is safe to resume.

        Returns:
            Kill switch status.

        Raises:
            ValueError: If actor or reason is missing.
        """
        await self.kill_switch.release(actor=actor, reason=reason)
        return self.kill_switch.status()

    # ------------------------------------------------------------------
    # Observación
    # ------------------------------------------------------------------

    async def health_report(self) -> dict[str, Any]:
        """Aggregate health across every production subsystem."""
        snapshot = await self.health.snapshot() if self.health is not None else None
        return {
            "health": snapshot.to_dict() if snapshot is not None else None,
            "safe_mode": self.safe_mode.status(),
            "kill_switch": self.kill_switch.status(),
            "recovery": self.recovery.status(),
            "mode": self.mode.status(),
        }

    def audit_event(
        self,
        *,
        action: AuditAction | str,
        actor: str = "system",
        target: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an arbitrary audited event.

        Args:
            action: Action name.
            actor: Who performed it.
            target: Optional subject.
            meta: Optional context.

        Returns:
            The stored entry.
        """
        return self.audit.record(action=action, actor=actor, target=target, meta=meta)

    def system_status(self) -> dict[str, Any]:
        """Compact production status for the dashboard."""
        report = self.gate.last_report
        return {
            "enabled": self._settings.production.enabled,
            "mode": self.mode.status(),
            "safe_mode": self.safe_mode.status(),
            "kill_switch": self.kill_switch.status(),
            "recovery": self.recovery.status(),
            "live_gate": report.to_dict() if report is not None else None,
            "approval": (
                self.approvals.current.to_dict() if self.approvals.current is not None else None
            ),
            "backup": self.backup.status() if self.backup is not None else None,
            "updates": self.updates.status() if self.updates is not None else None,
            "licenses": self.licenses.status() if self.licenses is not None else None,
            "maintenance": self.maintenance.status() if self.maintenance is not None else None,
            "failover": self.failover.status() if self.failover is not None else None,
        }

    # ------------------------------------------------------------------
    # Operación: backups, Notion, reportes, actualizaciones, seguridad
    # ------------------------------------------------------------------

    async def backup_database(self, *, kind: str = "manual") -> dict[str, Any]:
        """Create a backup of the critical state.

        Args:
            kind: ``manual`` or ``scheduled``.

        Returns:
            The backup record, or an error payload when backups are disabled.
        """
        if self.backup is None:
            return {"created": False, "reason": "backups deshabilitados"}
        record = await self.backup.acreate(kind=kind)
        return {"created": True, "backup": record.to_dict()}

    async def restore_database(self, *, backup_id: str, actor: str) -> dict[str, Any]:
        """Restore a backup by id (verifies integrity first).

        Args:
            backup_id: Backup identifier.
            actor: Who requests the restore (audited by the backup service).

        Returns:
            Result payload.

        Raises:
            FileNotFoundError: Unknown backup.
            ValueError: Corrupt backup (refused).
        """
        if self.backup is None:
            return {"restored": False, "reason": "backups deshabilitados"}
        _log.warning("Restore requested by %s: %s", actor, backup_id)
        return await self.backup.arestore(backup_id)

    def list_backups(self) -> list[dict[str, Any]]:
        """List available backups (newest first)."""
        if self.backup is None:
            return []
        return [record.to_dict() for record in self.backup.list_backups()]

    async def sync_notion(self) -> dict[str, Any]:
        """Flush the Notion documentation queue (retries pending entries)."""
        if self.documentation is None:
            return {"synced": False, "reason": "documentación no disponible"}
        result = await self.documentation.flush()
        self.audit.record(
            action=AuditAction.NOTION_SYNCED,
            actor="system",
            target="notion",
            after=result,
        )
        return {"synced": True, **result}

    async def send_daily_report(self) -> dict[str, Any]:
        """Build and deliver the daily operational report."""
        if self.reporting is None:
            return {"sent": False, "reason": "reportes deshabilitados"}
        report = await self.reporting.send_daily()
        return {"sent": True, "report": report.to_dict()}

    async def send_hourly_report(self) -> dict[str, Any]:
        """Build and deliver the hourly operational summary."""
        if self.reporting is None:
            return {"sent": False, "reason": "reportes deshabilitados"}
        report = await self.reporting.send_hourly()
        return {"sent": True, "report": report.to_dict()}

    def check_updates(self) -> dict[str, Any]:
        """Check for available updates against the configured manifest."""
        if self.updates is None:
            return {"available": False, "reason": "update manager deshabilitado"}
        return self.updates.check_for_updates().to_dict()

    async def run_improvement_analysis(self) -> dict[str, Any]:
        """Run the continuous-improvement analysis and register findings."""
        if self.improvement is None:
            return {"total": 0, "reason": "mejora continua deshabilitada"}
        report = await self.improvement.run()
        return report.to_dict()

    def security_report(self) -> dict[str, Any]:
        """Configuration validation + secret rotation status."""
        report = validation_report(self._settings)
        if self.secrets is not None:
            report["secrets"] = self.secrets.status(list(_TRACKED_SECRETS))
        return report


def _opt(value: Any) -> float | None:
    """Coerce a metric to float, mapping missing/invalid values to ``None``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _track_record_days(trades: Sequence[TradeRecord]) -> float:
    """Calendar days spanned by the paper trading track record.

    Args:
        trades: Journalled trades (oldest first).

    Returns:
        Days between the first entry and the last exit; ``0`` if under two
        trades — un solo día de historial no es un historial.
    """
    if len(trades) < 2:
        return 0.0
    first = min(trade.entry_time for trade in trades)
    last = max(trade.exit_time for trade in trades)
    return max(0.0, (last - first).total_seconds() / 86_400.0)

"""Constructor del reporte operativo (Fase 9).

Reúne, en el momento de emitir el reporte, el estado de todos los subsistemas
—rendimiento, salud, ML, brokers, Notion, Discord— en una foto única. Es
best-effort y aislado por subsistema, igual que los colectores de métricas: un
subsistema apagado o roto se omite y el reporte sale igual con lo que sí se
puede medir.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.container import Container
from app.documentation.service import DocumentationService
from app.execution.api import ExecutionCore
from app.ml.api import MLEngine
from app.monitoring.health import HealthMonitor
from app.notifications.service import NotificationService
from app.utils.time import utc_now

_log = logging.getLogger("app.production.reporting")


@dataclass(frozen=True, slots=True)
class OperationalReport:
    """A point-in-time operational report.

    Todos los campos son opcionales: lo que no se puede medir queda en ``None``
    o vacío, nunca inventado.
    """

    generated_at: str = field(default_factory=lambda: utc_now().isoformat())
    # Trading.
    balance: float | None = None
    equity: float | None = None
    pnl: float | None = None
    drawdown_pct: float | None = None
    win_rate: float | None = None
    profit_factor: float | None = None
    total_trades: int | None = None
    open_positions: int | None = None
    # Infra.
    cpu_pct: float | None = None
    memory_pct: float | None = None
    latency_ms: float | None = None
    recent_errors: int | None = None
    health_status: str = "unknown"
    # Estado de subsistemas.
    ml_status: str = "n/a"
    ml_drift: bool | None = None
    broker: str = "n/a"
    broker_connected: bool | None = None
    notion_pending: int = 0
    discord_delivered: int = 0
    discord_failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at,
            "trading": {
                "balance": self.balance,
                "equity": self.equity,
                "pnl": self.pnl,
                "drawdown_pct": self.drawdown_pct,
                "win_rate": self.win_rate,
                "profit_factor": self.profit_factor,
                "total_trades": self.total_trades,
                "open_positions": self.open_positions,
            },
            "infra": {
                "cpu_pct": self.cpu_pct,
                "memory_pct": self.memory_pct,
                "latency_ms": self.latency_ms,
                "recent_errors": self.recent_errors,
                "health_status": self.health_status,
            },
            "subsystems": {
                "ml_status": self.ml_status,
                "ml_drift": self.ml_drift,
                "broker": self.broker,
                "broker_connected": self.broker_connected,
                "notion_pending": self.notion_pending,
                "discord_delivered": self.discord_delivered,
                "discord_failed": self.discord_failed,
            },
        }

    def discord_fields(self) -> dict[str, str]:
        """Compact key/value fields for a Discord embed."""
        fields: dict[str, str] = {"Salud": self.health_status}
        if self.equity is not None:
            fields["Equity"] = f"{self.equity:,.2f}"
        if self.pnl is not None:
            fields["PnL"] = f"{self.pnl:,.2f}"
        if self.drawdown_pct is not None:
            fields["Drawdown"] = f"{self.drawdown_pct:.2f}%"
        if self.win_rate is not None:
            fields["Win rate"] = f"{self.win_rate * 100:.1f}%"
        if self.profit_factor is not None:
            fields["Profit factor"] = f"{self.profit_factor:.2f}"
        if self.total_trades is not None:
            fields["Trades"] = str(self.total_trades)
        if self.open_positions is not None:
            fields["Abiertas"] = str(self.open_positions)
        if self.cpu_pct is not None:
            fields["CPU"] = f"{self.cpu_pct:.0f}%"
        if self.memory_pct is not None:
            fields["RAM"] = f"{self.memory_pct:.0f}%"
        if self.latency_ms is not None:
            fields["Latencia"] = f"{self.latency_ms:.0f} ms"
        if self.recent_errors is not None:
            fields["Errores"] = str(self.recent_errors)
        fields["ML"] = self.ml_status + (" (drift)" if self.ml_drift else "")
        fields["Broker"] = self.broker
        if self.notion_pending:
            fields["Notion pend."] = str(self.notion_pending)
        return fields


def build_report(container: Container | None) -> OperationalReport:
    """Assemble an :class:`OperationalReport` from the live system.

    Args:
        container: DI container, or ``None`` when nothing is wired.

    Returns:
        The report (with whatever could be observed).
    """
    if container is None:
        return OperationalReport()
    data: dict[str, Any] = {}
    for collector in (_from_execution, _from_health, _from_ml, _from_notion, _from_discord):
        try:
            collector(container, data)
        except Exception:  # un subsistema roto no tumba el reporte entero
            _log.exception("Report collector %s failed", collector.__name__)
    return OperationalReport(**data)


def _from_execution(container: Container, data: dict[str, Any]) -> None:
    """Trading vitals from the Execution layer."""
    if not container.contains(ExecutionCore):
        return
    execution = container.resolve(ExecutionCore)
    positions = execution.positions.open_positions
    snapshot = execution.portfolio.snapshot(positions)
    performance = execution.performance.compute(execution.journal.all())
    data.update(
        balance=snapshot.balance,
        equity=snapshot.equity,
        pnl=performance.net_profit,
        drawdown_pct=snapshot.drawdown_pct,
        win_rate=performance.win_rate,
        profit_factor=performance.profit_factor,
        total_trades=performance.total_trades,
        open_positions=len(positions),
        broker=execution.engine.broker.broker_name,
        broker_connected=execution.engine.broker.healthcheck(),
    )


def _from_health(container: Container, data: dict[str, Any]) -> None:
    """System vitals from the Health Monitor."""
    if not container.contains(HealthMonitor):
        return
    snapshot = container.resolve(HealthMonitor).last_snapshot
    if snapshot is None:
        return
    data.update(
        cpu_pct=snapshot.cpu_percent,
        memory_pct=snapshot.memory_percent,
        latency_ms=snapshot.event_loop_lag_ms,
        recent_errors=len(snapshot.recent_errors),
        health_status=str(snapshot.status),
    )


def _from_ml(container: Container, data: dict[str, Any]) -> None:
    """Machine Learning status."""
    if not container.contains(MLEngine):
        return
    status = container.resolve(MLEngine).status()
    data["ml_status"] = str(status.get("active_model") or status.get("status") or "n/a")
    drift = status.get("drift")
    if isinstance(drift, dict):
        data["ml_drift"] = bool(drift.get("has_drift"))


def _from_notion(container: Container, data: dict[str, Any]) -> None:
    """Documentation sync backlog."""
    if not container.contains(DocumentationService):
        return
    data["notion_pending"] = container.resolve(DocumentationService).pending()


def _from_discord(container: Container, data: dict[str, Any]) -> None:
    """Notification delivery counters."""
    if not container.contains(NotificationService):
        return
    stats = container.resolve(NotificationService).stats
    data["discord_delivered"] = int(stats.get("delivered", 0))
    data["discord_failed"] = int(stats.get("failed", 0))

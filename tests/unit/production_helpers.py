"""Constructores compartidos de la capa de producción (Fase 9)."""

from pathlib import Path

from app.config.settings import (
    KillSwitchSettings,
    LiveGateSettings,
    RecoverySettings,
    SafeModeSettings,
)
from app.execution.risk_manager import RiskManager
from app.production.kill_switch import KillSwitchController
from app.production.live import ApprovalStore, GateInputs, LiveGate
from app.production.safe_mode import SafeModeController


def make_gate(tmp_path: Path | None = None, **criteria: object) -> tuple[LiveGate, ApprovalStore]:
    """Live Gate con su almacén de aprobaciones (en disco si se da tmp_path)."""
    settings = LiveGateSettings(**criteria)  # type: ignore[arg-type]
    path = (tmp_path / "approval.json") if tmp_path is not None else None
    approvals = ApprovalStore(path, ttl_hours=settings.approval_ttl_hours)
    return LiveGate(settings, approval_lookup=approvals.is_valid_for), approvals


def passing_inputs(**overrides: object) -> GateInputs:
    """Entradas que satisfacen TODOS los criterios menos la aprobación humana.

    Se usa para comprobar que el último criterio que falta es siempre el humano:
    ningún estado del sistema, por bueno que sea, habilita live por su cuenta.
    """
    base: dict[str, object] = {
        "allow_live": True,
        "mode_requested": "live",
        "environment": "production",
        "paper_trades": 500,
        "paper_days": 90.0,
        "profit_factor": 2.0,
        "sharpe": 1.8,
        "max_drawdown_pct": 5.0,
        "qualification_approved": True,
        "walk_forward_approved": True,
        "monte_carlo_approved": True,
        "ml_stable": True,
        "critical_drift": False,
        "test_coverage_pct": 92.0,
        "open_critical_errors": 0,
        "system_healthy": True,
        "broker_connected": True,
        "discord_ok": True,
        "database_ok": True,
        "redis_ok": True,
        "watchdog_ok": True,
        "scheduler_ok": True,
        "risk_manager_ok": True,
        "kill_switch_active": False,
        "safe_mode_active": False,
    }
    base.update(overrides)
    return GateInputs(**base)  # type: ignore[arg-type]


def make_safe_mode(**overrides: object) -> SafeModeController:
    """Safe Mode aislado (sin bus, auditoría ni Discord)."""
    return SafeModeController(SafeModeSettings(**overrides))  # type: ignore[arg-type]


def make_kill_switch(
    tmp_path: Path, risk: RiskManager | None = None, **overrides: object
) -> KillSwitchController:
    """Kill switch con estado persistido en tmp_path."""
    settings = KillSwitchSettings(
        state_path=tmp_path / "kill_switch.json",
        **overrides,  # type: ignore[arg-type]
    )
    return KillSwitchController(settings, risk)


def make_recovery_settings(tmp_path: Path, **overrides: object) -> RecoverySettings:
    """Ajustes de recuperación apuntando a tmp_path."""
    return RecoverySettings(
        snapshot_path=tmp_path / "state.json",
        **overrides,  # type: ignore[arg-type]
    )

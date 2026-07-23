"""Eventos de la capa de producción (solo campos primitivos, JSON-safe).

Se publican en el Event Bus para que Discord, el dashboard y la auditoría
reaccionen sin acoplarse a la implementación de los controladores.
"""

from dataclasses import dataclass, field

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class LiveGateEvaluated(Event):
    """Se evaluó el Live Gate (aprobado o no, con el detalle de cada criterio)."""

    approved: bool
    passed: int
    failed: int
    blocking_reasons: list[str] = field(default_factory=list)
    report_hash: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class LiveTradingEnabled(Event):
    """Se habilitó Live Trading tras superar el gate completo."""

    actor: str
    report_hash: str


@dataclass(frozen=True, kw_only=True, slots=True)
class LiveTradingDisabled(Event):
    """Se deshabilitó Live Trading (manual o automáticamente)."""

    actor: str
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class SafeModeEntered(Event):
    """El sistema entró en Safe Mode: no se abren posiciones nuevas."""

    trigger: str
    detail: str
    positions_closed: int = 0


@dataclass(frozen=True, kw_only=True, slots=True)
class SafeModeExited(Event):
    """El sistema salió de Safe Mode tras recuperarse de forma sostenida."""

    healthy_cycles: int


@dataclass(frozen=True, kw_only=True, slots=True)
class KillSwitchEngaged(Event):
    """Se activó el Kill Switch global: se detiene toda operativa nueva."""

    trigger: str
    reason: str
    actor: str = "system"


@dataclass(frozen=True, kw_only=True, slots=True)
class KillSwitchReleased(Event):
    """Se liberó el Kill Switch (siempre con actor y motivo)."""

    actor: str
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class RecoveryCompleted(Event):
    """El estado previo se rehidrató tras un reinicio."""

    positions_restored: int
    trades_restored: int
    snapshot_age_seconds: float
    kill_switch_restored: bool = False
    safe_mode_restored: bool = False

"""Safe Mode: degradación controlada cuando el sistema deja de ser fiable.

Es el escalón intermedio entre operar normal y el Kill Switch. Ante API caída,
broker inestable, latencia excesiva, drawdown crítico, memoria o CPU saturadas,
errores repetidos, drift severo o riesgo excesivo:

- se cierran las **entradas nuevas** (veto registrado en el Execution Engine),
- las posiciones abiertas se mantienen por defecto (cerrar a ciegas en un mal
  momento suele hacer más daño que esperar; es configurable),
- se alerta por Discord y se registra auditoría,
- y se espera recuperación **sostenida**: hacen falta N ciclos sanos seguidos
  para salir, para que el sistema no oscile entrando y saliendo cada 30
  segundos mientras la CPU baila alrededor del umbral.
"""

import logging
from datetime import datetime
from typing import Any

from app.config.settings import SafeModeSettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.notifications.models import Notification, NotificationLevel
from app.notifications.service import NotificationService
from app.production.audit import AuditAction, AuditLog
from app.production.events import SafeModeEntered, SafeModeExited
from app.production.safe_mode.triggers import SafeModeObservation, SafeModeTrigger
from app.utils.time import utc_now

_log = logging.getLogger("app.production.safe_mode")


class SafeModeController(Service):
    """Watch the system's vital signs and degrade operation when they slip.

    Args:
        settings: Safe Mode thresholds and behaviour.
        bus: Event Bus for publishing enter/exit events.
        audit: Audit log.
        notifications: Notification service (Discord).
    """

    def __init__(
        self,
        settings: SafeModeSettings,
        bus: EventBus | None = None,
        audit: AuditLog | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        super().__init__("safe_mode")
        self._settings = settings
        self._bus = bus
        self._audit = audit
        self._notifications = notifications
        self._active = False
        self._trigger: SafeModeTrigger | None = None
        self._detail = ""
        self._healthy_streak = 0
        self._entered_at: datetime | None = None

    @property
    def active(self) -> bool:
        """Whether Safe Mode is currently engaged."""
        return self._active

    @property
    def trigger(self) -> SafeModeTrigger | None:
        """What put the system into Safe Mode, if it is in it."""
        return self._trigger

    async def _on_start(self) -> None:
        """Nothing to acquire — evaluation is driven by the scheduler."""

    async def _on_stop(self) -> None:
        """Nothing to release."""

    def entry_veto(self) -> str | None:
        """Veto callback for the Execution Engine.

        Returns:
            A blocking reason while Safe Mode is active, else ``None``.
        """
        if not self._active:
            return None
        return f"Safe Mode activo ({self._trigger}): {self._detail}"

    async def evaluate(self, observation: SafeModeObservation) -> bool:
        """Evaluate the observation and enter or leave Safe Mode accordingly.

        Args:
            observation: Current vital signs.

        Returns:
            Whether Safe Mode is active after the evaluation.
        """
        if not self._settings.enabled:
            return False
        breach = self._first_breach(observation)
        if breach is not None:
            self._healthy_streak = 0
            if not self._active:
                await self._enter(*breach)
            return True
        if self._active:
            self._healthy_streak += 1
            if self._healthy_streak >= max(1, self._settings.recovery_cycles):
                await self._exit()
        return self._active

    def _first_breach(self, observation: SafeModeObservation) -> tuple[SafeModeTrigger, str] | None:
        """Return the first breached threshold, or ``None`` if all is well."""
        s = self._settings
        o = observation
        if o.api_ok is False:
            return (SafeModeTrigger.API_DOWN, "la API no responde")
        if o.broker_failures >= s.broker_failure_threshold > 0:
            return (
                SafeModeTrigger.BROKER_UNSTABLE,
                f"{o.broker_failures} fallos de broker ≥ {s.broker_failure_threshold}",
            )
        if o.latency_ms is not None and o.latency_ms > s.max_latency_ms:
            return (
                SafeModeTrigger.LATENCY,
                f"latencia {o.latency_ms:.0f} ms > {s.max_latency_ms:.0f} ms",
            )
        if o.drawdown_pct is not None and o.drawdown_pct >= s.max_drawdown_pct:
            return (
                SafeModeTrigger.DRAWDOWN,
                f"drawdown {o.drawdown_pct:.1f}% ≥ {s.max_drawdown_pct:.1f}%",
            )
        if o.memory_pct is not None and o.memory_pct >= s.max_memory_pct:
            return (
                SafeModeTrigger.MEMORY,
                f"memoria {o.memory_pct:.0f}% ≥ {s.max_memory_pct:.0f}%",
            )
        if o.cpu_pct is not None and o.cpu_pct >= s.max_cpu_pct:
            return (SafeModeTrigger.CPU, f"CPU {o.cpu_pct:.0f}% ≥ {s.max_cpu_pct:.0f}%")
        if o.recent_errors >= s.error_threshold > 0:
            return (
                SafeModeTrigger.REPEATED_ERRORS,
                f"{o.recent_errors} errores en {s.error_window_seconds:.0f}s "
                f"≥ {s.error_threshold}",
            )
        if o.critical_drift:
            return (SafeModeTrigger.DRIFT, "drift severo detectado por el ML")
        if o.risk_breach:
            return (SafeModeTrigger.RISK, "el Risk Manager reporta riesgo excesivo")
        return None

    async def _enter(self, trigger: SafeModeTrigger, detail: str) -> None:
        """Engage Safe Mode: block entries, alert, audit."""
        self._active = True
        self._trigger = trigger
        self._detail = detail
        self._entered_at = utc_now()
        self._healthy_streak = 0
        _log.warning("SAFE MODE activado (%s): %s", trigger, detail)
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.SAFE_MODE_ENTERED,
                actor="system",
                target="safe_mode",
                after={"trigger": str(trigger), "detail": detail},
            )
        if self._bus is not None:
            await self._bus.publish(
                SafeModeEntered(source="safe_mode", trigger=str(trigger), detail=detail)
            )
        await self._notify(
            "Safe Mode activado",
            f"{detail}\n\nNo se abrirán posiciones nuevas hasta que el sistema se "
            "recupere de forma sostenida.",
            NotificationLevel.CRITICAL,
        )

    async def _exit(self) -> None:
        """Leave Safe Mode after sustained recovery."""
        streak = self._healthy_streak
        _log.warning("Safe Mode desactivado tras %d ciclos sanos", streak)
        self._active = False
        self._trigger = None
        self._detail = ""
        self._entered_at = None
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.SAFE_MODE_EXITED,
                actor="system",
                target="safe_mode",
                meta={"healthy_cycles": streak},
            )
        if self._bus is not None:
            await self._bus.publish(SafeModeExited(source="safe_mode", healthy_cycles=streak))
        await self._notify(
            "Safe Mode desactivado",
            f"El sistema se mantuvo sano {streak} ciclos consecutivos. Se reanudan "
            "las entradas nuevas.",
            NotificationLevel.SUCCESS,
        )

    async def _notify(self, title: str, message: str, level: NotificationLevel) -> None:
        """Send a Discord notification (best effort)."""
        if self._notifications is None:
            return
        await self._notifications.notify(
            Notification(title=title, message=message, level=level, source="safe_mode")
        )

    def status(self) -> dict[str, Any]:
        """Full status for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "active": self._active,
            "trigger": str(self._trigger) if self._trigger else "",
            "detail": self._detail,
            "healthy_streak": self._healthy_streak,
            "recovery_cycles": self._settings.recovery_cycles,
            "entered_at": self._entered_at.isoformat() if self._entered_at else None,
        }

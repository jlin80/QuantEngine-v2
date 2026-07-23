"""Kill Switch global: el freno de emergencia del sistema.

El ``RiskManager`` de la Fase 5 ya tenía un kill switch, pero con tres huecos
que en producción son inaceptables y que este controlador cierra:

1. **No se podía disparar desde fuera.** ``engage_kill_switch`` no tenía ni un
   solo llamador fuera de su propio módulo: sólo saltaba por drawdown.
2. **No sobrevivía a un reinicio.** El estado vivía en memoria, así que
   reiniciar el proceso liberaba silenciosamente un switch activo.
3. **No quedaba registrado.** Ni el disparo ni la liberación se auditaban.

El aplanado de posiciones sigue siendo responsabilidad del Execution Engine
(``_refresh_risk``): este controlador activa el switch del Risk Manager y el
motor reacciona. Así no hay dos caminos distintos para cerrar posiciones.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.settings import KillSwitchSettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.execution.risk_manager import RiskManager
from app.production.audit import AuditAction, AuditLog
from app.production.events import KillSwitchEngaged, KillSwitchReleased
from app.production.kill_switch.sources import KillSwitchTrigger
from app.utils.time import utc_now

_log = logging.getLogger("app.production.kill_switch")


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    """Persisted kill switch state.

    Attributes:
        active: Whether the switch is engaged.
        trigger: What engaged it.
        reason: Human-readable explanation.
        actor: Who engaged it (``system`` for automatic triggers).
        engaged_at: UTC moment it was engaged.
    """

    active: bool
    trigger: str = ""
    reason: str = ""
    actor: str = ""
    engaged_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "active": self.active,
            "trigger": self.trigger,
            "reason": self.reason,
            "actor": self.actor,
            "engaged_at": self.engaged_at.isoformat() if self.engaged_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KillSwitchState":
        """Rebuild state from its persisted form.

        Args:
            data: Mapping produced by :meth:`to_dict`.

        Returns:
            The reconstructed state.
        """
        raw_moment = data.get("engaged_at")
        return cls(
            active=bool(data.get("active", False)),
            trigger=str(data.get("trigger", "")),
            reason=str(data.get("reason", "")),
            actor=str(data.get("actor", "")),
            engaged_at=datetime.fromisoformat(str(raw_moment)) if raw_moment else None,
        )


class KillSwitchController(Service):
    """Global emergency stop, triggerable from anywhere and persisted.

    Args:
        settings: Kill switch configuration.
        risk: Risk Manager whose switch is actually flipped (``None`` when
            execution is disabled — the controller still records state).
        bus: Event Bus for publishing engage/release events.
        audit: Audit log. Every engage and release is recorded, always.
    """

    def __init__(
        self,
        settings: KillSwitchSettings,
        risk: RiskManager | None,
        bus: EventBus | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        super().__init__("kill_switch")
        self._settings = settings
        self._risk = risk
        self._bus = bus
        self._audit = audit
        self._switch = KillSwitchState(active=False)

    @property
    def active(self) -> bool:
        """Whether the kill switch is currently engaged."""
        return self._switch.active

    @property
    def switch_state(self) -> KillSwitchState:
        """Current kill switch state (distinta del estado del ``Service``)."""
        return self._switch

    async def _on_start(self) -> None:
        """Restore a persisted engagement — a restart must not clear it."""
        restored = self._load()
        if restored is not None and restored.active:
            self._switch = restored
            if self._risk is not None:
                self._risk.engage_kill_switch(restored.reason or "kill switch persistido")
            _log.warning(
                "Kill switch restaurado tras reinicio: %s (%s)",
                restored.reason,
                restored.trigger,
            )
            self._record_audit(AuditAction.KILL_SWITCH_ENGAGED, restored, restored=True)

    async def _on_stop(self) -> None:
        """Persist the final state so the next boot sees it."""
        self._persist()

    async def engage(
        self,
        trigger: KillSwitchTrigger,
        reason: str,
        *,
        actor: str = "system",
    ) -> KillSwitchState:
        """Engage the kill switch (idempotent — re-engaging keeps the origin).

        Args:
            trigger: What caused it.
            reason: Human-readable explanation. Required by policy.
            actor: Who triggered it.

        Returns:
            The resulting state.
        """
        if self._switch.active:
            return self._switch
        self._switch = KillSwitchState(
            active=True,
            trigger=str(trigger),
            reason=reason,
            actor=actor,
            engaged_at=utc_now(),
        )
        if self._risk is not None:
            self._risk.engage_kill_switch(reason)
        self._persist()
        _log.warning("KILL SWITCH activado por %s (%s): %s", actor, trigger, reason)
        self._record_audit(AuditAction.KILL_SWITCH_ENGAGED, self._switch)
        if self._bus is not None:
            await self._bus.publish(
                KillSwitchEngaged(
                    source="kill_switch",
                    trigger=str(trigger),
                    reason=reason,
                    actor=actor,
                )
            )
        return self._switch

    async def release(self, *, actor: str, reason: str) -> KillSwitchState:
        """Release the kill switch. Never automatic, never anonymous.

        Args:
            actor: Who releases it. Must be identifiable.
            reason: Why it is safe to resume.

        Returns:
            The resulting state.

        Raises:
            ValueError: If the actor is empty, or a reason is required and
                missing. Liberar el freno de emergencia sin dejar constancia de
                quién y por qué es exactamente lo que no debe poder hacerse.
        """
        if not actor.strip():
            raise ValueError("Liberar el kill switch exige un actor identificable")
        if self._settings.require_reason_to_release and not reason.strip():
            raise ValueError("Liberar el kill switch exige un motivo")
        previous = self._switch
        self._switch = KillSwitchState(active=False)
        if self._risk is not None:
            self._risk.reset_kill_switch()
        self._persist()
        _log.warning("Kill switch liberado por %s: %s", actor, reason)
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.KILL_SWITCH_RELEASED,
                actor=actor,
                target="kill_switch",
                before=previous.to_dict(),
                after=self._switch.to_dict(),
                meta={"reason": reason},
            )
        if self._bus is not None:
            await self._bus.publish(
                KillSwitchReleased(source="kill_switch", actor=actor, reason=reason)
            )
        return self._switch

    async def check_scheduled(self, *, now: datetime | None = None) -> bool:
        """Engage the switch if the configured daily cut-off has arrived.

        Args:
            now: Reference moment (defaults to now, UTC).

        Returns:
            Whether the switch was engaged by this call.
        """
        raw = self._settings.scheduled_utc.strip()
        if not raw or self._switch.active:
            return False
        moment = now or utc_now()
        try:
            hour_str, minute_str = raw.split(":", 1)
            hour, minute = int(hour_str), int(minute_str)
        except ValueError:
            _log.warning("kill_switch.scheduled_utc inválido: %r (se ignora)", raw)
            return False
        if (moment.hour, moment.minute) != (hour, minute):
            return False
        await self.engage(
            KillSwitchTrigger.SCHEDULED,
            f"corte programado diario a las {raw} UTC",
            actor="scheduler",
        )
        return True

    def status(self) -> dict[str, Any]:
        """Full status for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "scheduled_utc": self._settings.scheduled_utc,
            **self._switch.to_dict(),
        }

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _path(self) -> Path | None:
        """Configured state file, or ``None`` when persistence is disabled."""
        return self._settings.state_path if self._settings.persist_state else None

    def _load(self) -> KillSwitchState | None:
        """Read the persisted state (best effort)."""
        path = self._path()
        if path is None or not path.exists():
            return None
        try:
            return KillSwitchState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            _log.warning("No se pudo leer el estado del kill switch: %r", exc)
            return None

    def _persist(self) -> None:
        """Write the current state (best effort)."""
        path = self._path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._switch.to_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            _log.warning("No se pudo persistir el estado del kill switch: %r", exc)

    def _record_audit(
        self, action: AuditAction, state: KillSwitchState, *, restored: bool = False
    ) -> None:
        """Record an engagement in the audit trail."""
        if self._audit is None:
            return
        self._audit.record(
            action=action,
            actor=state.actor or "system",
            target="kill_switch",
            after=state.to_dict(),
            meta={"restored_after_restart": restored},
        )

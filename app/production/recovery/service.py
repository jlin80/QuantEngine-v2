"""RecoveryService: al reiniciar, nunca se empieza de cero.

Hasta la Fase 8 un reinicio era amnesia total: ``PositionManager``,
``PortfolioManager`` y ``RiskManager`` se construían frescos, el ``TradeJournal``
arrancaba con el deque vacío aunque su fichero tuviera miles de líneas, y un
kill switch activo se liberaba solo. Con posiciones abiertas de verdad, eso es
un agujero operativo, no una molestia.

Este servicio captura el estado vivo cada pocos segundos y lo rehidrata al
arrancar, **antes** de que el Execution Engine empiece a operar.
"""

import json
import logging
from typing import Any

from app.config.settings import RecoverySettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.execution.execution_engine import ExecutionEngine
from app.execution.models import Position, TradeRecord
from app.production.audit import AuditAction, AuditLog
from app.production.events import RecoveryCompleted
from app.production.recovery.snapshot import (
    EngineState,
    StateSnapshotStore,
    position_from_state,
    position_to_state,
    trade_from_dict,
)
from app.utils.time import utc_now

_log = logging.getLogger("app.production.recovery")


class RecoveryService(Service):
    """Capture and restore the engine's operating state across restarts.

    Args:
        settings: Recovery configuration.
        store: Snapshot store.
        execution: Execution Engine whose state is captured/restored.
            ``None`` when execution is disabled (nothing to recover).
        bus: Event Bus.
        audit: Audit log.
    """

    def __init__(
        self,
        settings: RecoverySettings,
        store: StateSnapshotStore,
        execution: ExecutionEngine | None = None,
        bus: EventBus | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        super().__init__("recovery")
        self._settings = settings
        self._store = store
        self._execution = execution
        self._bus = bus
        self._audit = audit
        self._restored = False
        self._last_report: dict[str, Any] = {}

    @property
    def restored(self) -> bool:
        """Whether a restore has already run in this process."""
        return self._restored

    async def _on_start(self) -> None:
        """Restore the previous state, if any and recent enough."""
        if self._settings.enabled:
            await self.restore()

    async def _on_stop(self) -> None:
        """Take a final snapshot so the next boot starts where we left off."""
        if self._settings.enabled:
            self.capture()

    # ------------------------------------------------------------------
    # Captura
    # ------------------------------------------------------------------

    def capture(self) -> EngineState | None:
        """Snapshot the current engine state.

        Returns:
            The captured state, or ``None`` if there is no execution engine.
        """
        if self._execution is None:
            return None
        positions = self._execution.positions.open_positions
        portfolio = self._execution.portfolio
        risk_status = self._execution.risk.status()
        state = EngineState(
            captured_at=utc_now(),
            positions=[position_to_state(position) for position in positions],
            balance=portfolio.balance,
            peak_equity=portfolio.peak_equity,
            total_trades=portfolio.total_trades,
            wins=portfolio.wins,
            losses=portfolio.losses,
            commission_paid=portfolio.commission_paid,
            consecutive_losses=_as_int(risk_status.get("consecutive_losses")),
            kill_switch_active=bool(risk_status.get("kill_switch", False)),
        )
        self._store.save(state)
        return state

    # ------------------------------------------------------------------
    # Restauración
    # ------------------------------------------------------------------

    async def restore(self) -> dict[str, Any]:
        """Rehydrate positions, accounting, risk state and the trade journal.

        Returns:
            A report of what was restored (empty-ish when there was nothing).
        """
        report: dict[str, Any] = {
            "positions_restored": 0,
            "trades_restored": 0,
            "snapshot_age_seconds": 0.0,
            "kill_switch_restored": False,
            "skipped_reason": "",
        }
        if self._execution is None:
            report["skipped_reason"] = "sin motor de ejecución"
            self._last_report = report
            return report

        state = self._store.load()
        if state is None:
            report["skipped_reason"] = "no hay snapshot previo"
        else:
            age = state.age_seconds()
            report["snapshot_age_seconds"] = age
            max_age = self._settings.max_age_hours * 3600.0
            if max_age > 0 and age > max_age:
                # Un snapshot viejo describe un mercado que ya no existe:
                # rehidratar posiciones de hace días sería peor que ignorarlas.
                _log.warning("Snapshot descartado por antigüedad (%.0f s > %.0f s)", age, max_age)
                report["skipped_reason"] = f"snapshot demasiado viejo ({age / 3600:.1f} h)"
            else:
                report.update(self._apply_state(state, self._execution))

        report["trades_restored"] = self._restore_journal()
        self._restored = True
        self._last_report = report
        if any(
            (
                report["positions_restored"],
                report["trades_restored"],
                report["kill_switch_restored"],
            )
        ):
            _log.warning(
                "Recuperación: %d posiciones, %d operaciones del journal",
                report["positions_restored"],
                report["trades_restored"],
            )
            if self._audit is not None:
                self._audit.record(
                    action=AuditAction.SYSTEM_RECOVERED,
                    actor="system",
                    target="engine",
                    after=report,
                )
            if self._bus is not None:
                await self._bus.publish(
                    RecoveryCompleted(
                        source="recovery",
                        positions_restored=int(report["positions_restored"]),
                        trades_restored=int(report["trades_restored"]),
                        snapshot_age_seconds=float(report["snapshot_age_seconds"]),
                        kill_switch_restored=bool(report["kill_switch_restored"]),
                    )
                )
        return report

    def _apply_state(self, state: EngineState, execution: ExecutionEngine) -> dict[str, Any]:
        """Push a loaded snapshot into the live managers.

        Args:
            state: Snapshot to apply.
            execution: Live execution engine to rehydrate.

        Returns:
            What was restored.
        """
        positions: list[Position] = []
        for raw in state.positions:
            try:
                positions.append(position_from_state(raw))
            except (KeyError, ValueError, TypeError) as exc:
                # Una posición ilegible no puede tumbar toda la recuperación.
                _log.error("Posición no rehidratable, se omite: %r", exc)
        restored = execution.positions.restore(positions)
        execution.portfolio.restore(
            balance=state.balance,
            peak_equity=state.peak_equity,
            total_trades=state.total_trades,
            wins=state.wins,
            losses=state.losses,
            commission_paid=state.commission_paid,
        )
        execution.risk.restore(consecutive_losses=state.consecutive_losses)
        kill_restored = False
        if state.kill_switch_active:
            execution.risk.engage_kill_switch("kill switch restaurado tras reinicio")
            kill_restored = True
        return {"positions_restored": restored, "kill_switch_restored": kill_restored}

    def _restore_journal(self) -> int:
        """Reload the Trade Journal from its own JSONL file."""
        if not self._settings.reload_journal or self._execution is None:
            return 0
        journal = self._execution.journal
        path = journal.path
        if path is None or not path.exists():
            return 0
        trades: list[TradeRecord] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        trades.append(trade_from_dict(json.loads(line)))
                    except (ValueError, KeyError, TypeError) as exc:
                        _log.warning("Línea del journal ilegible, se omite: %r", exc)
        except OSError as exc:
            _log.error("No se pudo releer el journal: %r", exc)
            return 0
        return journal.restore(trades)

    def status(self) -> dict[str, Any]:
        """Full recovery status for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "restored": self._restored,
            "last_report": dict(self._last_report),
            "snapshot_path": str(self._settings.snapshot_path),
        }


def _as_int(value: object) -> int:
    """Coerce a loosely-typed status value to int (0 when unusable)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    return 0

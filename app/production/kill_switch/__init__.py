"""Kill Switch global (manual, automático, programado, por riesgo)."""

from app.production.kill_switch.controller import KillSwitchController, KillSwitchState
from app.production.kill_switch.sources import KillSwitchTrigger

__all__ = ["KillSwitchController", "KillSwitchState", "KillSwitchTrigger"]

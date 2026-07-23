"""Safe Mode: degradación controlada ante señales vitales malas."""

from app.production.safe_mode.controller import SafeModeController
from app.production.safe_mode.triggers import SafeModeObservation, SafeModeTrigger

__all__ = ["SafeModeController", "SafeModeObservation", "SafeModeTrigger"]

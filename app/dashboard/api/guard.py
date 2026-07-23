"""Safety guards for dashboard writes — above all, never enable live trading.

Fase 9 (live trading) stays disabled: ``resolved_mode()`` forces ``paper`` and no
dashboard write may flip the engine to live. These helpers reject any attempt to
set a live/real/production mode from a config patch or command payload.
"""

from typing import Any

from fastapi import HTTPException

_LIVE_TOKENS = frozenset({"live", "real", "production", "prod"})
_LIVE_MESSAGE = "Live trading is disabled (Fase 9). Mode cannot be set to live."


def forbid_live_value(value: Any) -> None:
    """Reject a value that would select a live/real trading mode.

    Args:
        value: Candidate value for a mode-like setting.

    Raises:
        HTTPException: 403 if the value names a live mode.
    """
    if isinstance(value, str) and value.strip().lower() in _LIVE_TOKENS:
        raise HTTPException(status_code=403, detail=_LIVE_MESSAGE)


def assert_no_live_switch(patch: dict[str, Any]) -> None:
    """Reject any patch entry that could enable live trading.

    Args:
        patch: Mapping of setting keys to new values.

    Raises:
        HTTPException: 403 if a key/value pair would enable live trading.
    """
    for key, value in patch.items():
        lowered = key.lower()
        if "mode" in lowered or "live" in lowered:
            forbid_live_value(value)
            if "live" in lowered and isinstance(value, bool) and value:
                raise HTTPException(status_code=403, detail=_LIVE_MESSAGE)

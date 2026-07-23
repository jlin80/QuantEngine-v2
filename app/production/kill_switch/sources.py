"""Fuentes de disparo del Kill Switch global."""

import enum


class KillSwitchTrigger(enum.StrEnum):
    """Por qué se activó el Kill Switch."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"
    SCHEDULED = "scheduled"
    RISK = "risk"
    DRAWDOWN = "drawdown"
    ERRORS = "errors"
    CONNECTION_LOST = "connection_lost"
    LATENCY = "latency"
    DATA_LOSS = "data_loss"
    INCONSISTENCY = "inconsistency"

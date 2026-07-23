"""Disparadores de Safe Mode y la observación que los alimenta."""

import enum
from dataclasses import dataclass


class SafeModeTrigger(enum.StrEnum):
    """Motivo por el que el sistema entra en Safe Mode."""

    API_DOWN = "api_down"
    BROKER_UNSTABLE = "broker_unstable"
    LATENCY = "latency"
    DRAWDOWN = "drawdown"
    MEMORY = "memory"
    CPU = "cpu"
    REPEATED_ERRORS = "repeated_errors"
    DRIFT = "drift"
    RISK = "risk"


@dataclass(frozen=True, slots=True)
class SafeModeObservation:
    """Snapshot of the signals Safe Mode watches.

    Todo es opcional: un valor desconocido no dispara Safe Mode (a diferencia
    del Live Gate, que falla cerrado). La asimetría es deliberada — el gate
    decide si *empezar* a arriesgar dinero, y Safe Mode si *parar*. Ser
    conservador significa cosas opuestas en cada caso, y degradar la operativa
    por un sensor que no reporta sería peor que el problema.
    """

    api_ok: bool | None = None
    broker_failures: int = 0
    latency_ms: float | None = None
    drawdown_pct: float | None = None
    memory_pct: float | None = None
    cpu_pct: float | None = None
    recent_errors: int = 0
    critical_drift: bool = False
    risk_breach: bool = False

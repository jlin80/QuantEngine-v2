"""Presupuesto de CPU del ciclo autónomo del Research Lab.

El laboratorio comparte VPS con el motor que está operando. Un ciclo de
generación son cientos de backtests; sin techo explícito puede robarle CPU al
bucle de gestión de posiciones, que es el único que no puede llegar tarde.

Este módulo decide **si el ciclo puede arrancar ahora** y **cuánto trabajo tiene
permitido**. No ejecuta nada: devuelve una decisión explicada, para que el motor
la aplique y la registre. Así se puede probar sin levantar el laboratorio.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config.settings import ResearchBudgetSettings
from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class BudgetDecision:
    """Whether the research cycle may run, and with what limits."""

    allowed: bool
    reason: str
    max_symbols: int = 0
    max_generated: int = 0
    timeout_seconds: float = 0.0
    max_workers: int = 1

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "max_symbols": self.max_symbols,
            "max_generated": self.max_generated,
            "timeout_seconds": self.timeout_seconds,
            "max_workers": self.max_workers,
        }


def in_window(settings: ResearchBudgetSettings, moment: datetime | None = None) -> bool:
    """Whether ``moment`` falls inside the configured low-activity window.

    Una ventana con ``start == end`` significa **siempre**: es la forma
    explícita de desactivar la restricción horaria sin desactivar el resto del
    presupuesto. Si ``start > end`` la ventana cruza medianoche.
    """
    start = settings.window_start_hour_utc % 24
    end = settings.window_end_hour_utc % 24
    if start == end:
        return True
    hour = (moment or utc_now()).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def evaluate_budget(
    settings: ResearchBudgetSettings,
    *,
    moment: datetime | None = None,
    cpu_pct: float | None = None,
    open_positions: int = 0,
) -> BudgetDecision:
    """Decide whether the autonomous research cycle may run right now.

    Args:
        settings: Presupuesto configurado.
        moment: Momento de referencia (inyectable en tests).
        cpu_pct: CPU actual del host; ``None`` si no se puede medir — en ese
            caso **no se bloquea**: un sensor mudo no debe parar el laboratorio,
            igual que Safe Mode no degrada la operativa por falta de lectura.
        open_positions: Posiciones abiertas ahora mismo.

    Returns:
        La decisión, con sus límites y el motivo (también cuando permite).
    """
    limits: dict[str, Any] = {
        "max_symbols": max(1, settings.max_symbols_per_run),
        "max_generated": max(1, settings.max_generated_per_run),
        # Suelo mínimo sólo para impedir un timeout de 0 (que cancelaría el
        # ciclo antes de empezar); el valor útil lo pone la configuración.
        "timeout_seconds": max(1.0, settings.run_timeout_seconds),
        "max_workers": max(1, settings.max_workers),
    }

    if not settings.enabled:
        # Sin presupuesto activo no se relajan los límites: se deniega. Un
        # laboratorio sin techo en la VPS que opera es justo lo que se evita.
        return BudgetDecision(
            allowed=False,
            reason="El presupuesto del Research Lab está desactivado.",
            **limits,
        )
    if not in_window(settings, moment):
        return BudgetDecision(
            allowed=False,
            reason=(
                f"Fuera de la ventana de baja actividad "
                f"({settings.window_start_hour_utc:02d}:00-"
                f"{settings.window_end_hour_utc:02d}:00 UTC)."
            ),
            **limits,
        )
    if settings.skip_if_positions_open and open_positions > 0:
        return BudgetDecision(
            allowed=False,
            reason=(
                f"Hay {open_positions} posición(es) abierta(s): el laboratorio puede "
                f"esperar, la gestión de una posición viva no."
            ),
            **limits,
        )
    if cpu_pct is not None and cpu_pct > settings.skip_if_cpu_pct_above:
        return BudgetDecision(
            allowed=False,
            reason=(
                f"CPU al {cpu_pct:.0f}% (límite {settings.skip_if_cpu_pct_above:.0f}%): "
                f"se pospone al próximo disparo."
            ),
            **limits,
        )
    return BudgetDecision(allowed=True, reason="Dentro del presupuesto.", **limits)

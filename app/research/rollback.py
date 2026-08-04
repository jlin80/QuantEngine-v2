"""Desactivación automática del ciclo autónomo si el motor operativo se degrada.

El presupuesto del Bloque 6 decide **si el ciclo puede arrancar**. Esto decide
algo distinto: si, una vez activado, el ciclo hay que **apagarlo**.

Son problemas separados y por eso son módulos separados. El presupuesto mira el
estado *antes* de empezar (ventana, CPU, posiciones abiertas) y su peor caso es
posponer un ciclo. Esta vigilancia mira el efecto *sobre el motor* y su peor
caso es haber estado degradando la operativa sin que nadie lo notase — que es la
lección del incidente del reloj: lo que no se mide no avisa.

**Sólo apaga el laboratorio.** Nunca toca la operativa, nunca cierra posiciones y
no puede habilitar live. El sentido de la asimetría es deliberado: ante la duda,
el que se sacrifica es el research.

**No se rearma solo.** Volver a activar `auto_cycle` es una decisión humana. Un
rollback que se revierte automáticamente convertiría un problema persistente en
un ciclo de encendido/apagado, que es más difícil de diagnosticar que el fallo.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import ResearchRollbackSettings
from app.utils.time import clock_skew_seconds

_LOG = logging.getLogger("app.research.rollback")

CPU = "cpu_sustained"
MANAGE_LATENCY = "manage_loop_latency"
CLOCK_SKEW = "clock_skew"
PIPELINE_ALARM = "pipeline_alarm"


@dataclass(frozen=True, kw_only=True, slots=True)
class RollbackTrigger:
    """Un motivo concreto por el que el ciclo autónomo debe apagarse.

    Attributes:
        name: Identificador del disparador.
        detail: Qué se midió, con números (un aviso sin cifras no es accionable).
    """

    name: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {"name": self.name, "detail": self.detail}


@dataclass(kw_only=True, slots=True)
class ResearchRollbackMonitor:
    """Watch the operating engine and pull the plug on the research cycle.

    Args:
        settings: Umbrales de rollback.
        baseline_latency_seconds: Latencia de referencia del bucle de gestión.
            ``None`` mientras no haya muestra suficiente — y entonces **no se
            dispara**: comparar contra una línea base que no existe es cómo se
            fabrican los falsos positivos que acaban desactivando la vigilancia.
    """

    settings: ResearchRollbackSettings
    _cpu_breaches: int = field(default=0, init=False)
    _alarms: set[str] = field(default_factory=set, init=False)
    _triggered: tuple[RollbackTrigger, ...] = field(default=(), init=False)

    # ------------------------------------------------------------------
    # Entradas
    # ------------------------------------------------------------------

    def on_pipeline_alarm(self, name: str) -> None:
        """Record a pipeline alarm (`MarketDataBlind` / `SignalDrought`).

        El motor ciego o mudo es la degradación más grave que existe. No hace
        falta demostrar que la causó el research: con el motor sin operar, el
        laboratorio no tiene ninguna prioridad.
        """
        self._alarms.add(name)

    def on_pipeline_recovered(self, name: str) -> None:
        """Clear a pipeline alarm that reported recovery."""
        self._alarms.discard(name)

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        cpu_pct: float | None = None,
        manage_ema_seconds: float | None = None,
        baseline_latency_seconds: float | None = None,
        manage_passes: int = 0,
    ) -> tuple[RollbackTrigger, ...]:
        """Evaluate every rollback condition against the current readings.

        Args:
            cpu_pct: CPU del host; ``None`` = sensor mudo, que **no dispara**
                (misma regla que el presupuesto y que Safe Mode).
            manage_ema_seconds: EMA de la latencia del bucle de gestión.
            baseline_latency_seconds: Latencia de referencia con la que comparar.
            manage_passes: Pasadas acumuladas del bucle; con muestra escasa no
                se juzga la latencia.

        Returns:
            Los disparadores activos (vacío si el motor está sano).
        """
        triggers: list[RollbackTrigger] = []

        if cpu_pct is not None and cpu_pct > self.settings.max_cpu_pct:
            self._cpu_breaches += 1
            if self._cpu_breaches >= self.settings.cpu_breaches_before_rollback:
                triggers.append(
                    RollbackTrigger(
                        name=CPU,
                        detail=(
                            f"CPU al {cpu_pct:.0f}% (límite {self.settings.max_cpu_pct:.0f}%) "
                            f"en {self._cpu_breaches} muestras consecutivas."
                        ),
                    )
                )
        elif cpu_pct is not None:
            # Una racha rota vuelve a cero: un pico aislado no es degradación.
            self._cpu_breaches = 0

        if (
            manage_ema_seconds is not None
            and baseline_latency_seconds is not None
            and baseline_latency_seconds > 0.0
            and manage_passes >= self.settings.min_manage_passes
        ):
            ratio = manage_ema_seconds / baseline_latency_seconds
            if ratio > self.settings.max_manage_latency_ratio:
                triggers.append(
                    RollbackTrigger(
                        name=MANAGE_LATENCY,
                        detail=(
                            f"El bucle de gestión de posiciones tarda {ratio:.1f}× su "
                            f"referencia ({manage_ema_seconds * 1000:.0f} ms vs "
                            f"{baseline_latency_seconds * 1000:.0f} ms). Es el único "
                            f"bucle que no puede llegar tarde."
                        ),
                    )
                )

        skew = abs(clock_skew_seconds())
        if skew > self.settings.max_clock_skew_seconds:
            triggers.append(
                RollbackTrigger(
                    name=CLOCK_SKEW,
                    detail=(
                        f"Desviación del reloj de {skew:.1f}s: hay un reloj simulado "
                        f"filtrado fuera de su contexto."
                    ),
                )
            )

        if self._alarms:
            triggers.append(
                RollbackTrigger(
                    name=PIPELINE_ALARM,
                    detail=(
                        f"Alarma(s) de pipeline activa(s): {', '.join(sorted(self._alarms))}. "
                        f"Con el motor sin operar, el laboratorio no tiene prioridad."
                    ),
                )
            )

        self._triggered = tuple(triggers)
        return self._triggered

    @property
    def triggered(self) -> tuple[RollbackTrigger, ...]:
        """Disparadores de la última evaluación."""
        return self._triggered

    def status(self) -> dict[str, Any]:
        """Compact monitor status (dashboard/diagnóstico)."""
        return {
            "cpu_breaches": self._cpu_breaches,
            "active_alarms": sorted(self._alarms),
            "triggered": [t.to_dict() for t in self._triggered],
        }

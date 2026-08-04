"""Vigilancia del pipeline: detecta que el motor dejó de operar en silencio.

El 2026-07-31 un reloj simulado se filtró al proceso del motor y el validador
empezó a descartar el **100% de los ticks**. Estuvo así 4 días. Nada avisó: el
proceso respondía a la API, todos los servicios figuraban ``running`` y el
watchdog de componentes no tenía nada que reportar. El motor no estaba caído —
había dejado de ver el mercado, que no es lo mismo y **no se parecía a un fallo**
en ninguna métrica existente.

Este servicio vigila el **efecto**, no aquella causa concreta (de esa se encarga
``health.max_clock_skew_seconds``). Da igual qué lo provoque —un reloj, un
proveedor que cambia el formato de timestamp, un símbolo mal mapeado, un
despliegue a medias—: si el motor se queda ciego o mudo, hay que enterarse el
primer día.

Dos alarmas complementarias:

- **Ciego** (``MarketDataBlind``): entran datos, pero se descartan casi todos.
- **Mudo** (``SignalDrought``): entran datos limpios y aun así no sale ni una
  señal.

Ambas se miden por **deltas entre muestras**, no sobre contadores acumulados: un
acumulado diluye el presente, y tras un incidente largo seguiría marcando rojo
mucho después de haberse recuperado.

La alarma de "mudo" exige datos limpios fluyendo, y por eso **no necesita un
calendario de sesiones**: con el mercado cerrado no hay ticks, no se cumple la
condición y no avisa. Un calendario habría que mantenerlo y se equivocaría en
festivos; el flujo de datos es la evidencia directa de que el mercado está vivo.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from app.config.settings import PipelineWatchSettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.monitoring.events import MarketDataBlind, MarketDataRecovered, SignalDrought
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService

StatsProvider = Callable[[], dict[str, int]]
"""Devuelve los contadores acumulados del validador (``checked``/``discarded``)."""

SignalCountProvider = Callable[[], int]
"""Devuelve el total acumulado de señales producidas por las estrategias."""


class PipelineWatchdog(Service):
    """Watch for an engine that silently stopped seeing or speaking.

    Args:
        settings: Umbrales y cadencia de la vigilancia.
        bus: Event Bus donde publicar las alarmas.
        stats_provider: Contadores del validador de datos.
        signal_count_provider: Total de señales producidas.
        notifications: Servicio de notificaciones (Discord). Opcional: sin él
            la alarma sigue publicándose en el bus y quedando en el log.
    """

    def __init__(
        self,
        settings: PipelineWatchSettings,
        bus: EventBus,
        *,
        stats_provider: StatsProvider,
        signal_count_provider: SignalCountProvider,
        notifications: NotificationService | None = None,
    ) -> None:
        super().__init__("pipeline_watchdog")
        self._settings = settings
        self._bus = bus
        self._stats = stats_provider
        self._signals = signal_count_provider
        self._notifications = notifications
        self._prev_checked = 0
        self._prev_discarded = 0
        self._prev_signals = 0
        self._primed = False
        self._silent_windows = 0
        # Latches: una alarma que se repite cada 5 minutos se acaba silenciando,
        # y una alarma silenciada es peor que ninguna.
        self._blind_announced = False
        self._drought_announced = False
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.pipeline_watchdog")

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------

    async def evaluate(self) -> dict[str, Any]:
        """Sample the counters once and raise/clear the alarms.

        Returns:
            Lo observado en esta ventana (para el dashboard y los tests).
        """
        stats = self._stats()
        checked = int(stats.get("checked", 0))
        discarded = int(stats.get("discarded", 0))
        signals = self._signals()

        # La primera pasada sólo fija la línea base: sin una muestra anterior no
        # hay delta, y arrancar comparando contra cero daría una alarma falsa
        # justo al iniciar el motor.
        if not self._primed:
            self._prev_checked, self._prev_discarded, self._prev_signals = (
                checked,
                discarded,
                signals,
            )
            self._primed = True
            return {"status": "priming"}

        delta_checked = max(0, checked - self._prev_checked)
        delta_discarded = max(0, discarded - self._prev_discarded)
        delta_signals = max(0, signals - self._prev_signals)
        self._prev_checked, self._prev_discarded, self._prev_signals = (
            checked,
            discarded,
            signals,
        )

        delta_clean = delta_checked - delta_discarded
        ratio = delta_discarded / delta_checked if delta_checked else 0.0

        await self._check_blind(delta_checked, delta_discarded, ratio)
        await self._check_drought(delta_clean, delta_signals)

        return {
            "checked": delta_checked,
            "discarded": delta_discarded,
            "clean": delta_clean,
            "discard_ratio": round(ratio, 4),
            "signals": delta_signals,
            "silent_windows": self._silent_windows,
            "blind": self._blind_announced,
            "drought": self._drought_announced,
        }

    async def _check_blind(self, checked: int, discarded: int, ratio: float) -> None:
        """Raise (or clear) the "engine is blind" alarm."""
        if checked < self._settings.min_samples:
            return  # muestra insuficiente: no se afirma nada en ningún sentido
        if ratio >= self._settings.blind_discard_ratio:
            if not self._blind_announced:
                self._blind_announced = True
                self._log.error(
                    "El motor está CIEGO: descartando el %.0f%% de los datos (%d de %d).",
                    ratio * 100,
                    discarded,
                    checked,
                )
                await self._publish(
                    MarketDataBlind(
                        source="pipeline_watchdog",
                        checked=checked,
                        discarded=discarded,
                        discard_ratio=round(ratio, 4),
                        detail=(
                            f"Se está descartando el {ratio * 100:.0f}% de los datos de "
                            f"mercado ({discarded} de {checked} en la última ventana). "
                            f"Sin datos válidos no hay velas, ni señales, ni operaciones: "
                            f"el motor sigue vivo pero no ve el mercado. Revisa el reloj "
                            f"del proceso (`/api/system/status` → `clock_skew_seconds`) y "
                            f"los avisos de `app.market.validator`."
                        ),
                    )
                )
                await self._notify(
                    "🙈 El motor está CIEGO: no ve el mercado",
                    f"Se está descartando el **{ratio * 100:.0f}%** de los datos de "
                    f"mercado. Sin datos válidos no hay velas, ni señales, ni "
                    f"operaciones — y el proceso sigue vivo, así que nada más lo "
                    f"delata.",
                    NotificationLevel.CRITICAL,
                    {
                        "Descartados": f"{discarded} de {checked}",
                        "Tasa": f"{ratio * 100:.0f}%",
                        "Primero a revisar": "`/api/system/status` → `clock_skew_seconds`",
                    },
                )
        elif self._blind_announced:
            self._blind_announced = False
            self._log.info("El motor vuelve a ver el mercado (descarte %.0f%%).", ratio * 100)
            await self._publish(
                MarketDataRecovered(
                    source="pipeline_watchdog",
                    checked=checked,
                    discard_ratio=round(ratio, 4),
                    detail=f"Descarte de vuelta al {ratio * 100:.0f}%: datos de mercado sanos.",
                )
            )
            await self._notify(
                "✅ El motor vuelve a ver el mercado",
                f"El descarte bajó al **{ratio * 100:.0f}%**: datos de mercado sanos.",
                NotificationLevel.SUCCESS,
                {"Tasa": f"{ratio * 100:.0f}%", "Revisados": str(checked)},
            )

    async def _check_drought(self, clean: int, signals: int) -> None:
        """Raise (or clear) the "engine is mute" alarm."""
        if clean < self._settings.min_clean_samples:
            # Sin datos limpios no se puede distinguir "mudo" de "mercado
            # cerrado". No se cuenta la ventana ni se limpia el contador: se
            # ignora, y la racha se retoma cuando vuelva a haber datos.
            return
        if signals > 0:
            if self._drought_announced:
                self._drought_announced = False
                self._log.info("Vuelven a producirse señales (%d en la ventana).", signals)
            self._silent_windows = 0
            return

        self._silent_windows += 1
        if self._silent_windows < self._settings.silent_windows or self._drought_announced:
            return
        self._drought_announced = True
        minutes = self._silent_windows * self._settings.check_interval_seconds / 60.0
        self._log.error(
            "El motor está MUDO: %.0f min con datos limpios y sin una sola señal.", minutes
        )
        await self._publish(
            SignalDrought(
                source="pipeline_watchdog",
                minutes=round(minutes, 1),
                clean_samples=clean,
                detail=(
                    f"Llevan {minutes:.0f} minutos entrando datos de mercado limpios "
                    f"({clean} en la última ventana) sin que ninguna estrategia haya "
                    f"emitido una señal. El mercado está vivo y el motor no dice nada: "
                    f"revisa si las estrategias están cargadas y habilitadas, y si algún "
                    f"filtro las está vetando a todas."
                ),
            )
        )
        await self._notify(
            "🔇 El motor está MUDO: cero señales con el mercado vivo",
            f"Llevan **{minutes:.0f} minutos** entrando datos de mercado limpios sin "
            f"que ninguna estrategia emita una señal. El mercado está vivo y el motor "
            f"no dice nada.",
            NotificationLevel.ERROR,
            {
                "Silencio": f"{minutes:.0f} min",
                "Datos limpios": f"{clean} en la última ventana",
                "A revisar": "estrategias cargadas/habilitadas y filtros con veto",
            },
        )

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Sample on its own cadence, tolerating failures."""
        while True:
            await asyncio.sleep(self._settings.check_interval_seconds)
            try:
                await self.evaluate()
            except Exception:  # la vigilancia jamás puede tumbar el motor
                self._log.exception("Pipeline watchdog pass failed")

    async def _on_start(self) -> None:
        if self._settings.enabled:
            self._task = asyncio.create_task(self._loop(), name="pipeline-watchdog")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _publish(self, event: Any) -> None:
        """Publish tolerating a stopped/saturated bus."""
        with contextlib.suppress(Exception):
            await self._bus.publish(event)

    async def _notify(
        self, title: str, message: str, level: NotificationLevel, fields: dict[str, str]
    ) -> None:
        """Deliver the alarm to Discord (best effort)."""
        if self._notifications is None:
            return
        with contextlib.suppress(Exception):
            await self._notifications.send(
                title, message, level=level, fields=fields, source="pipeline_watchdog"
            )

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "blind": self._blind_announced,
            "drought": self._drought_announced,
            "silent_windows": self._silent_windows,
        }

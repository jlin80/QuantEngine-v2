"""Servicio que mide la calidad del dato en bucle (Bloque 11).

Separado del motor por la misma razón que en los bloques anteriores: el motor
es aritmética pura y se puede probar sin montar nada; esto es lo que le da un
reloj, una fuente de métricas y una cadencia.
"""

import asyncio
import contextlib
import logging
from typing import Any

from app.config.settings import DataQualitySettings
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.monitoring.data_quality import DataQualityEngine
from app.monitoring.events import DataQualityDegraded
from app.monitoring.meta_risk import MetaRiskEngine


class DataQualityMonitor(Service):
    """Measure data health on a cadence and announce degradations.

    Args:
        settings: Configuración del motor.
        engine: Motor que hace la medición.
        bus: Event Bus donde avisar.
        meta_risk: Motor de riesgo de infraestructura (Bloque 12), que consume
            el multiplicador de calidad y produce el compuesto.
    """

    def __init__(
        self,
        settings: DataQualitySettings,
        engine: DataQualityEngine,
        bus: EventBus | None = None,
        meta_risk: MetaRiskEngine | None = None,
    ) -> None:
        super().__init__("data_quality")
        self._settings = settings
        self._engine = engine
        self._bus = bus
        # El Meta Risk Engine (Bloque 12) se mide en el mismo ciclo y despues
        # de la calidad del dato, porque la consume: medirlo en un bucle propio
        # significaria componer con una lectura de calidad de hasta un minuto
        # de antiguedad, justo cuando lo que cambia rapido es la calidad.
        self._meta_risk = meta_risk
        self._degraded = False
        self._cycles = 0
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.monitoring.data_quality.service")

    async def run_cycle(self) -> None:
        """Take one measurement and announce a change of state.

        Sólo se anuncia la **transición**, en las dos direcciones: entrar en
        degradación y salir de ella. Repetir el aviso cada minuto convertiría la
        alarma en ruido, y no anunciar la recuperación dejaría a quien la leyó
        creyendo que el problema sigue.
        """
        report = self._engine.measure()
        if self._meta_risk is not None:
            self._meta_risk.measure()
        self._cycles += 1
        if self._bus is None or report.degraded == self._degraded:
            self._degraded = report.degraded
            return
        self._degraded = report.degraded
        await self._bus.publish(
            DataQualityDegraded(
                source="data_quality",
                score=report.score or 0.0,
                risk_multiplier=report.risk_multiplier,
                degraded=report.degraded,
                reasons="; ".join(report.reasons),
            )
        )

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {"cycles": self._cycles, **self._engine.status()}

    async def _loop(self) -> None:
        """Measure forever on the configured cadence."""
        while True:
            await asyncio.sleep(self._settings.cycle_interval_seconds)
            try:
                await self.run_cycle()
            except Exception:  # medir calidad jamás puede tumbar al motor
                self._log.exception("Data quality cycle failed")

    async def _on_start(self) -> None:
        if not self._settings.enabled:
            return
        # Una medición inmediata: arrancar con el multiplicador en 1.0 durante
        # un ciclo entero es arrancar sin la protección que este bloque existe
        # para dar, justo en el momento en que el feed aún se está estabilizando.
        try:
            await self.run_cycle()
        except Exception:
            self._log.exception("Initial data quality measurement failed")
        self._task = asyncio.create_task(self._loop(), name="data-quality")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

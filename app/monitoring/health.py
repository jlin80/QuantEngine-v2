"""Health Monitor: estado global del sistema en tiempo real.

Recolecta CPU, RAM, disco, uptime, latencia interna del event loop, estado
de módulos (vía watchdog), métricas del Event Bus y errores recientes.
Publica :class:`ModuleHealthChanged` cuando el estado global cambia.
"""

import asyncio
import contextlib
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

from app.config.settings import HealthSettings
from app.core.events.bus import EventBus
from app.core.events.events import ModuleHealthChanged
from app.core.lifecycle import Service
from app.logging.recent import get_recent_errors
from app.monitoring.watchdog import Watchdog
from app.utils.time import clock_skew_seconds, isoformat_utc


class HealthStatus(enum.StrEnum):
    """Estado global del sistema."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Fotografía completa de la salud del sistema."""

    status: HealthStatus
    timestamp: str
    uptime_seconds: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    disk_percent: float
    event_loop_lag_ms: float
    clock_skew_seconds: float
    event_bus: dict[str, int]
    components: dict[str, str]
    recent_errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (API/dashboard)."""
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "memory_used_mb": round(self.memory_used_mb, 1),
            "disk_percent": self.disk_percent,
            "event_loop_lag_ms": round(self.event_loop_lag_ms, 2),
            "clock_skew_seconds": round(self.clock_skew_seconds, 3),
            "event_bus": self.event_bus,
            "components": self.components,
            "recent_errors": self.recent_errors,
        }


class HealthMonitor(Service):
    """Periodic system health sampler.

    Args:
        settings: Health section of the configuration.
        bus: Event bus (metrics source + destination of health events).
        watchdog: Optional watchdog to report per-component status.
    """

    def __init__(
        self,
        settings: HealthSettings,
        bus: EventBus,
        watchdog: Watchdog | None = None,
    ) -> None:
        super().__init__("health_monitor")
        self._settings = settings
        self._bus = bus
        self._watchdog = watchdog
        self._started_at = time.monotonic()
        self._task: asyncio.Task[None] | None = None
        self._last_status = HealthStatus.HEALTHY
        self._last_snapshot: HealthSnapshot | None = None
        self._log = logging.getLogger("app.health_monitor")

    @property
    def last_snapshot(self) -> HealthSnapshot | None:
        """Most recent snapshot (``None`` before the first sample)."""
        return self._last_snapshot

    async def snapshot(self) -> HealthSnapshot:
        """Collect a fresh health snapshot.

        Returns:
            The freshly sampled :class:`HealthSnapshot`.
        """
        lag_ms = await self._event_loop_lag_ms()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(".")
        components: dict[str, str] = {}
        if self._watchdog is not None:
            components = {
                name: status.value for name, status in self._watchdog.component_statuses.items()
            }
        recent = [
            {"timestamp": e.timestamp, "level": e.level, "logger": e.logger, "message": e.message}
            for e in get_recent_errors()
        ]
        # Desviación entre el reloj efectivo del proceso y el de pared. En vivo
        # debe ser 0: cualquier otra cosa significa que un reloj simulado de
        # backtest se filtró fuera de su contexto. Es el fallo que dejó al motor
        # 4 días descartando el 100% de los ticks sin que nada avisara — no
        # estaba caído, sólo ciego, y ninguna métrica lo reflejaba.
        skew = clock_skew_seconds()
        status = self._evaluate(
            cpu=psutil.cpu_percent(interval=None),
            memory_pct=memory.percent,
            disk_pct=disk.percent,
            components=components,
            clock_skew=skew,
        )
        snap = HealthSnapshot(
            status=status,
            timestamp=isoformat_utc(),
            uptime_seconds=time.monotonic() - self._started_at,
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=memory.percent,
            memory_used_mb=memory.used / (1024 * 1024),
            disk_percent=disk.percent,
            event_loop_lag_ms=lag_ms,
            clock_skew_seconds=skew,
            event_bus=self._bus.stats.to_dict(),
            components=components,
            recent_errors=recent[-10:],
        )
        self._last_snapshot = snap
        return snap

    def _evaluate(
        self,
        *,
        cpu: float,
        memory_pct: float,
        disk_pct: float,
        components: dict[str, str],
        clock_skew: float = 0.0,
    ) -> HealthStatus:
        """Derive the overall status from resource usage and components."""
        frozen = [name for name, status in components.items() if status == "frozen"]
        if frozen:
            return HealthStatus.UNHEALTHY
        # Un reloj desviado no degrada: invalida. Con el reloj mal, el validador
        # de mercado descarta todos los ticks y el motor deja de operar en
        # silencio, así que esto es UNHEALTHY, no un aviso.
        if abs(clock_skew) > self._settings.max_clock_skew_seconds:
            self._log.error(
                "Reloj del motor desviado %.1fs del reloj de pared: un reloj "
                "simulado se ha filtrado fuera de su contexto. El motor "
                "descartará los ticks del broker mientras dure.",
                clock_skew,
            )
            return HealthStatus.UNHEALTHY
        if (
            cpu >= self._settings.cpu_warn_pct
            or memory_pct >= self._settings.memory_warn_pct
            or disk_pct >= self._settings.disk_warn_pct
        ):
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    @staticmethod
    async def _event_loop_lag_ms() -> float:
        """Measure event-loop scheduling lag (sleep drift)."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(0.05)
        return max(0.0, (loop.time() - start - 0.05) * 1000)

    async def _monitor_loop(self) -> None:
        """Sample periodically and publish transitions."""
        while True:
            try:
                snap = await self.snapshot()
                if snap.status is not self._last_status:
                    self._log.warning(
                        "System health changed: %s -> %s", self._last_status, snap.status
                    )
                    await self._bus.publish(
                        ModuleHealthChanged(
                            source=self.name,
                            module="system",
                            previous=self._last_status.value,
                            current=snap.status.value,
                        )
                    )
                    self._last_status = snap.status
            except Exception:
                self._log.exception("Health sampling failed")
            await asyncio.sleep(self._settings.check_interval_seconds)

    async def _on_start(self) -> None:
        psutil.cpu_percent(interval=None)  # primer muestreo (cebado)
        self._task = asyncio.create_task(self._monitor_loop(), name="health-monitor")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

"""QuantEngine: orquestador del ciclo de vida de todos los servicios."""

import asyncio
import logging

from app import __version__
from app.cache.service import CacheService
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.core.events.events import SystemStarted, SystemStopping
from app.core.lifecycle import Service
from app.dashboard.api.service import ApiService
from app.engine.evaluation import PerformanceTracker
from app.engine.state_manager import HistoryWriter
from app.engine.strategy_engine import StrategyEngine
from app.execution.api import ExecutionCore
from app.execution.execution_engine import ExecutionEngine
from app.execution.notifications import ExecutionNotifier
from app.market.collector import TickCollector
from app.market.feed import MarketFeed
from app.market.scheduler import register_market_jobs
from app.market.storage import MarketDataWriter
from app.ml.api import MLEngine
from app.ml.notifications import MLNotifier
from app.monitoring.health import HealthMonitor
from app.monitoring.watchdog import Watchdog
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService
from app.production.api import ProductionAPI
from app.production.kill_switch import KillSwitchController
from app.production.recovery import RecoveryService
from app.production.safe_mode import SafeModeController, SafeModeObservation
from app.research.notifications import ResearchNotifier
from app.scheduler.scheduler import AsyncScheduler


class QuantEngine:
    """Owns the ordered startup/shutdown of every service.

    Args:
        settings: Central configuration.
        container: DI container built by :func:`app.engine.bootstrap.build_container`.
    """

    def __init__(self, settings: Settings, container: Container) -> None:
        self._settings = settings
        self._container = container
        self._stop_event = asyncio.Event()
        self._log = logging.getLogger("app.engine")
        # Orden de arranque: el bus primero (todos publican en él),
        # la API al final (expone lo ya construido). Apagado en orden inverso.
        self._services: list[Service] = [
            container.resolve(EventBus),
            container.resolve(CacheService),
            container.resolve(NotificationService),
            container.resolve(AsyncScheduler),
            container.resolve(Watchdog),
            container.resolve(HealthMonitor),
        ]
        # Data Engine (si está habilitado): storage → pipeline → feed.
        if container.contains(MarketFeed):
            self._services.extend(
                [
                    container.resolve(MarketDataWriter),
                    container.resolve(TickCollector),
                    container.resolve(MarketFeed),
                ]
            )
        # Quant Core (si está habilitado): historial → evaluación → estrategias.
        if container.contains(StrategyEngine):
            self._services.extend(
                [
                    container.resolve(HistoryWriter),
                    container.resolve(PerformanceTracker),
                    container.resolve(StrategyEngine),
                ]
            )
        # Producción (Fase 9): la recuperación va ANTES del Execution Engine —
        # rehidratar posiciones después de que el motor empiece a gestionarlas
        # sería una carrera contra su propio bucle.
        if container.contains(RecoveryService):
            self._services.append(container.resolve(RecoveryService))
        # Execution Engine (Fase 5): el notificador primero (para captar los
        # eventos desde el arranque), luego el orquestador. Paper trading.
        if container.contains(ExecutionEngine):
            self._services.extend(
                [
                    container.resolve(ExecutionNotifier),
                    container.resolve(ExecutionEngine),
                ]
            )
        # Controladores de seguridad: después del motor, porque el kill switch
        # restaurado necesita un Risk Manager ya construido al que aplicarse.
        if container.contains(SafeModeController):
            self._services.extend(
                [
                    container.resolve(SafeModeController),
                    container.resolve(KillSwitchController),
                ]
            )
        # Machine Learning (Fase 7): sólo el notificador es un servicio (se
        # suscribe al bus). El MLEngine es una fachada sin ciclo de vida; sus
        # tareas (entrenamiento/deriva/meta) las dispara el scheduler.
        if container.contains(MLNotifier):
            self._services.append(container.resolve(MLNotifier))
        # Quant Research Lab (Fase 10): sólo el notificador es un servicio (se
        # suscribe al bus). El ResearchLab es una fachada sin ciclo de vida; sus
        # ciclos (generación/validación/shadow) los dispara el scheduler o el
        # dashboard. El laboratorio nunca opera ni habilita live trading.
        if container.contains(ResearchNotifier):
            self._services.append(container.resolve(ResearchNotifier))
        self._services.append(container.resolve(ApiService))

    @property
    def container(self) -> Container:
        """DI container (composition root output)."""
        return self._container

    async def start(self) -> None:
        """Start every service in order and announce readiness."""
        self._log.info(
            "Starting Quant Engine v%s [%s]", __version__, self._settings.environment.value
        )
        for service in self._services:
            await service.start()

        self._register_watchdog_components()
        self._register_periodic_jobs()

        bus = self._container.resolve(EventBus)
        await bus.publish(
            SystemStarted(
                source="engine",
                environment=self._settings.environment.value,
                version=__version__,
            )
        )
        notifications = self._container.resolve(NotificationService)
        await notifications.send(
            "🟢 Quant Engine iniciado",
            f"Sistema operativo en ambiente **{self._settings.environment.value}** "
            f"(v{__version__}). Fase 5: Execution Engine — **paper trading** "
            f"(sin live). Riesgo, cartera y journal activos.",
            level=NotificationLevel.SUCCESS,
            source="engine",
        )
        self._log.info("Quant Engine started")

    def _register_watchdog_components(self) -> None:
        """Put every service (except the watchdog itself) under supervision."""
        watchdog = self._container.resolve(Watchdog)
        scheduler = self._container.resolve(AsyncScheduler)
        for service in self._services:
            if isinstance(service, Watchdog):
                continue
            watchdog.register(
                service.name,
                restart_callback=self._make_restarter(service),
            )

        async def heartbeat_job() -> None:
            for service in self._services:
                if isinstance(service, Watchdog):
                    continue
                if await service.healthcheck():
                    watchdog.heartbeat(service.name)

        interval = max(1.0, self._settings.watchdog.check_interval_seconds / 2)
        scheduler.add_job(
            "watchdog_heartbeats", heartbeat_job, interval_seconds=interval, run_immediately=True
        )

    @staticmethod
    def _make_restarter(service: Service) -> "_Restarter":
        """Build the restart callback for a supervised service."""
        return _Restarter(service)

    def _register_periodic_jobs(self) -> None:
        """Register Phase-1 periodic maintenance jobs."""
        scheduler = self._container.resolve(AsyncScheduler)
        notifications = self._container.resolve(NotificationService)
        health = self._container.resolve(HealthMonitor)

        async def hourly_health_report() -> None:
            snap = await health.snapshot()
            await notifications.send(
                "📊 Reporte de salud",
                f"Estado: **{snap.status.value}**",
                level=NotificationLevel.INFO,
                fields={
                    "CPU": f"{snap.cpu_percent:.0f}%",
                    "RAM": f"{snap.memory_percent:.0f}%",
                    "Disco": f"{snap.disk_percent:.0f}%",
                    "Uptime": f"{snap.uptime_seconds / 3600:.1f} h",
                    "Eventos": str(snap.event_bus.get("dispatched", 0)),
                },
                source="health_monitor",
            )

        scheduler.add_job("hourly_health_report", hourly_health_report, interval_seconds=3600)

        # Reportes periódicos del Execution Engine (si está habilitado).
        if self._container.contains(ExecutionCore):
            core = self._container.resolve(ExecutionCore)
            report_interval = max(60.0, self._settings.execution.report_interval_seconds)

            async def hourly_execution_report() -> None:
                await core.send_periodic_report("📊 Resumen de trading (última hora)")

            async def daily_execution_report() -> None:
                await core.send_periodic_report("🗓️ Resumen diario de trading")

            scheduler.add_job(
                "hourly_execution_report",
                hourly_execution_report,
                interval_seconds=report_interval,
            )
            scheduler.add_job(
                "daily_execution_report", daily_execution_report, interval_seconds=86_400
            )

        # Aprendizaje continuo del ML (Fase 7): entrenamiento nocturno, chequeo
        # de deriva y gobierno de estrategias. El ML asesora, nunca opera: estos
        # jobs sólo producen modelos/recomendaciones y publican eventos; jamás
        # abren posiciones ni habilitan live trading.
        if self._container.contains(MLEngine):
            ml_engine = self._container.resolve(MLEngine)
            meta_interval = max(60.0, self._settings.ml.meta.evaluation_interval_seconds)

            async def ml_nightly_training() -> None:
                await ml_engine.run_nightly_training()

            async def ml_drift_check() -> None:
                await ml_engine.run_drift_check()

            async def ml_meta_evaluation() -> None:
                await ml_engine.run_meta_evaluation()

            scheduler.add_job("ml_nightly_training", ml_nightly_training, interval_seconds=86_400)
            scheduler.add_job("ml_drift_check", ml_drift_check, interval_seconds=21_600)
            scheduler.add_job(
                "ml_meta_evaluation", ml_meta_evaluation, interval_seconds=meta_interval
            )

        # Producción (Fase 9): vigilancia de safe mode, corte programado del
        # kill switch, snapshot de estado y re-evaluación del Live Gate. Ningún
        # job puede habilitar live: el gate sólo *evalúa*, y la aprobación del
        # operador es un acto humano en el dashboard.
        if self._container.contains(ProductionAPI):
            production = self._container.resolve(ProductionAPI)
            safe_interval = max(5.0, self._settings.health.check_interval_seconds)
            snapshot_interval = max(
                5.0, self._settings.production.recovery.snapshot_interval_seconds
            )

            async def safe_mode_watch() -> None:
                await production.safe_mode.evaluate(await self._observe_vitals())

            async def kill_switch_schedule() -> None:
                await production.kill_switch.check_scheduled()

            async def state_snapshot() -> None:
                production.recovery.capture()

            async def live_gate_evaluation() -> None:
                production.gate.evaluate(production.collect_gate_inputs())

            scheduler.add_job("safe_mode_watch", safe_mode_watch, interval_seconds=safe_interval)
            scheduler.add_job("kill_switch_schedule", kill_switch_schedule, interval_seconds=60.0)
            scheduler.add_job("state_snapshot", state_snapshot, interval_seconds=snapshot_interval)
            scheduler.add_job("live_gate_evaluation", live_gate_evaluation, interval_seconds=300.0)

            self._register_operational_jobs(scheduler, production)

    def _register_operational_jobs(
        self, scheduler: AsyncScheduler, production: ProductionAPI
    ) -> None:
        """Register the Fase 9 operational jobs: reports, backups, sync, upkeep.

        Ninguno abre operaciones ni habilita live: observan, respaldan,
        sincronizan documentación y proponen mejoras. Cada bloque se registra
        sólo si su subsistema está cableado.
        """
        prod = self._settings.production

        if production.reporting is not None and prod.reporting.enabled:
            if prod.reporting.hourly:

                async def production_hourly_report() -> None:
                    await production.send_hourly_report()

                scheduler.add_job(
                    "production_hourly_report",
                    production_hourly_report,
                    interval_seconds=max(60.0, prod.reporting.hourly_interval_seconds),
                )
            if prod.reporting.daily:

                async def production_daily_report() -> None:
                    await production.send_daily_report()

                scheduler.add_job(
                    "production_daily_report",
                    production_daily_report,
                    interval_seconds=max(300.0, prod.reporting.daily_interval_seconds),
                )

        if production.backup is not None and prod.backup.enabled:

            async def scheduled_backup() -> None:
                await production.backup_database(kind="scheduled")

            scheduler.add_job(
                "scheduled_backup",
                scheduled_backup,
                interval_seconds=max(300.0, prod.backup.interval_seconds),
                jitter_seconds=60.0,
            )

        if production.documentation is not None and self._settings.notion.enabled:

            async def notion_sync() -> None:
                await production.sync_notion()

            scheduler.add_job("notion_sync", notion_sync, interval_seconds=300.0)

        if production.improvement is not None:

            async def improvement_analysis() -> None:
                await production.run_improvement_analysis()

            scheduler.add_job(
                "improvement_analysis",
                improvement_analysis,
                interval_seconds=max(3600.0, prod.improvement.interval_seconds),
                jitter_seconds=120.0,
            )

        if production.maintenance is not None and prod.maintenance.enabled:
            maintenance = production.maintenance

            async def maintenance_cleanup() -> None:
                await maintenance.acleanup()

            scheduler.add_job(
                "maintenance_cleanup",
                maintenance_cleanup,
                interval_seconds=max(600.0, prod.maintenance.cleanup_interval_seconds),
            )

        if production.failover is not None and prod.failover.enabled:
            failover = production.failover

            async def failover_heartbeat() -> None:
                failover.heartbeat()

            scheduler.add_job(
                "failover_heartbeat",
                failover_heartbeat,
                interval_seconds=max(2.0, prod.failover.renew_interval_seconds),
                run_immediately=True,
            )

        # Jobs del Data Engine (si está habilitado).
        if self._container.contains(MarketFeed):
            register_market_jobs(
                scheduler,
                self._container.resolve(MarketFeed),
                self._container.resolve(TickCollector),
                self._container.resolve(CacheService),
            )

    async def _observe_vitals(self) -> SafeModeObservation:
        """Collect the vital signs Safe Mode watches.

        Returns:
            The current observation. Los valores que no se pueden medir se
            dejan en ``None``: Safe Mode no degrada la operativa por un sensor
            que no reporta.
        """
        health = self._container.resolve(HealthMonitor)
        snapshot = health.last_snapshot
        drawdown: float | None = None
        risk_breach = False
        if self._container.contains(ExecutionCore):
            execution = self._container.resolve(ExecutionCore)
            status = execution.risk.status()
            raw_drawdown = status.get("drawdown_pct")
            drawdown = float(raw_drawdown) if isinstance(raw_drawdown, int | float) else None
            risk_breach = bool(status.get("circuit_breaker", False))
        if snapshot is None:
            return SafeModeObservation(drawdown_pct=drawdown, risk_breach=risk_breach)
        return SafeModeObservation(
            cpu_pct=snapshot.cpu_percent,
            memory_pct=snapshot.memory_percent,
            latency_ms=snapshot.event_loop_lag_ms,
            drawdown_pct=drawdown,
            recent_errors=len(snapshot.recent_errors),
            risk_breach=risk_breach,
        )

    async def run_forever(self) -> None:
        """Block until :meth:`request_stop` is called."""
        await self._stop_event.wait()

    def request_stop(self) -> None:
        """Signal the engine to shut down (safe from signal handlers)."""
        self._stop_event.set()

    async def stop(self) -> None:
        """Stop every service in reverse order (never raises)."""
        self._log.info("Stopping Quant Engine")
        bus = self._container.resolve(EventBus)
        if bus.is_running:
            try:
                await bus.publish(SystemStopping(source="engine"))
            except Exception:
                self._log.exception("Could not publish SystemStopping")
        notifications = self._container.resolve(NotificationService)
        if notifications.is_running:
            await notifications.send(
                "🔴 Quant Engine detenido",
                f"Apagado ordenado en ambiente **{self._settings.environment.value}**.",
                level=NotificationLevel.WARNING,
                source="engine",
            )
        for service in reversed(self._services):
            await service.stop()
        self._log.info("Quant Engine stopped")


class _Restarter:
    """Callable that restarts a service (used by the watchdog)."""

    def __init__(self, service: Service) -> None:
        self._service = service

    async def __call__(self) -> None:
        """Stop and start the wrapped service."""
        await self._service.stop()
        await self._service.start()

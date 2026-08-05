"""QuantEngine: orquestador del ciclo de vida de todos los servicios."""

import asyncio
import logging
from typing import Any

from app import __version__
from app.cache.service import CacheService
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.events.events import SystemStarted, SystemStopping
from app.core.lifecycle import Service
from app.dashboard.api.service import ApiService
from app.engine.attribution import EdgeAttributionEngine, FactorCapture
from app.engine.correlation import CorrelationEngine
from app.engine.edge_research import EdgeResearchEngine
from app.engine.evaluation import PerformanceTracker
from app.engine.meta_governance import MetaGovernanceApplier
from app.engine.regime_forecast import RegimeForecastService
from app.engine.startup_guard import verify_clean_startup
from app.engine.state_manager import HistoryWriter
from app.engine.strategy_engine import StrategyEngine
from app.execution.api import ExecutionCore
from app.execution.execution_engine import ExecutionEngine
from app.execution.notifications import ExecutionNotifier
from app.market.collector import TickCollector
from app.market.feed import MarketFeed
from app.market.models import Timeframe
from app.market.scheduler import register_market_jobs
from app.market.services import MarketDataService
from app.market.storage import MarketDataWriter
from app.ml.api import MLEngine
from app.ml.notifications import MLNotifier
from app.monitoring.data_quality_service import DataQualityMonitor
from app.monitoring.events import MarketDataBlind, MarketDataRecovered, SignalDrought
from app.monitoring.health import HealthMonitor
from app.monitoring.pipeline_watch import PipelineWatchdog
from app.monitoring.watchdog import Watchdog
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService
from app.production.api import ProductionAPI
from app.production.kill_switch import KillSwitchController
from app.production.recovery import RecoveryService
from app.production.safe_mode import SafeModeController, SafeModeObservation
from app.research.api import ResearchLab
from app.research.budget import evaluate_budget
from app.research.events import ResearchCycleRolledBack
from app.research.notifications import ResearchNotifier
from app.research.rollback import ResearchRollbackMonitor
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
        # Vigilancia de rollback del ciclo autónomo de research. Se construye
        # siempre (es barato y sin estado externo); sólo actúa si el ciclo está
        # activo, que por defecto no lo está.
        self._research_rollback = ResearchRollbackMonitor(settings=settings.research.rollback)
        self._manage_latency_baseline: float | None = None
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
        # Edge Research Engine (Bloque 1): va DESPUÉS del evaluador, porque mide
        # sobre las resoluciones que ese escribe. Sólo observa.
        if container.contains(EdgeResearchEngine):
            self._services.append(container.resolve(EdgeResearchEngine))
        # Captura de factores (Bloque 2): se suscribe al bus, asi que arranca
        # antes que el motor que consume lo que captura.
        if container.contains(FactorCapture):
            self._services.append(container.resolve(FactorCapture))
        if container.contains(EdgeAttributionEngine):
            self._services.append(container.resolve(EdgeAttributionEngine))
        # Pronostico de regimen (Bloque 4): siembra su muestra del historico al
        # arrancar, asi que va despues de que el mercado este servido.
        if container.contains(RegimeForecastService):
            self._services.append(container.resolve(RegimeForecastService))
        if container.contains(CorrelationEngine):
            self._services.append(container.resolve(CorrelationEngine))
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
        # Aplicador del gobierno del Meta Strategy Manager: sí es un servicio,
        # porque se suscribe al bus. Traduce los pesos/activaciones del MSM a
        # configuración del Strategy Engine, auditando cada cambio. Sólo mueve
        # configuración: no opera, no toca código y no puede habilitar live.
        if container.contains(MetaGovernanceApplier):
            self._services.append(container.resolve(MetaGovernanceApplier))
        # Vigilancia del pipeline: avisa si el motor se queda ciego (descarta
        # los datos) o mudo (no emite señales). Va después del Strategy Engine
        # porque muestrea sus contadores.
        if container.contains(PipelineWatchdog):
            self._services.append(container.resolve(PipelineWatchdog))
        # Calidad del dato (Bloque 11): va con la vigilancia del pipeline, y
        # mide en cuanto arranca — un ciclo entero con el multiplicador en 1.0
        # es un ciclo sin la proteccion que este bloque existe para dar.
        if container.contains(DataQualityMonitor):
            self._services.append(container.resolve(DataQualityMonitor))
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
        """Start every service in order and announce readiness.

        Raises:
            ContaminatedStartupError: Si el proceso arranca con instrumentación
                de test o backtest activa en `paper`/`production`.
        """
        self._log.info(
            "Starting Quant Engine v%s [%s]", __version__, self._settings.environment.value
        )
        # Antes de levantar nada: el proceso no puede traer un reloj simulado ni
        # instrumentación de test. Arrancar y operar con estado contaminado es
        # lo que costó 4 días de ceguera silenciosa (ADR-091/095).
        verify_clean_startup(self._settings)
        self._watch_pipeline_alarms()
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

            # Experimentos con fecha de corte por estrategia (Bloque 2). El job
            # abre los experimentos pendientes y adjudica los vencidos: mide la
            # expectativa de la estrategia dentro de la ventana y, si sigue en
            # negativo con muestra suficiente, la marca como candidata a
            # desactivación y avisa por Discord. **Nunca desactiva nada**:
            # apagar una estrategia es mover `strategies_enabled` a mano.
            experiments_cfg = self._settings.execution.experiments
            if experiments_cfg.enabled:
                experiment_interval = max(60.0, experiments_cfg.check_interval_seconds)

                async def strategy_experiment_check() -> None:
                    await core.engine.run_strategy_experiments()

                scheduler.add_job(
                    "strategy_experiment_check",
                    strategy_experiment_check,
                    interval_seconds=experiment_interval,
                    run_immediately=True,
                )

            # Falsación del cambio de holding por estrategia (Bloque 7.1). Mide
            # la predicción del Bloque 1 en su ventana y notifica el veredicto
            # **acierte o falle**: "se desplegó sin errores" no es evidencia.
            falsification_cfg = self._settings.execution.falsification
            if falsification_cfg.enabled:
                falsification_interval = max(60.0, falsification_cfg.check_interval_seconds)

                async def holding_falsification_check() -> None:
                    await core.engine.run_holding_falsification()

                scheduler.add_job(
                    "holding_falsification_check",
                    holding_falsification_check,
                    interval_seconds=falsification_interval,
                    run_immediately=True,
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

            # Segunda capa sobre el gate de validación (Bloque 5): un modelo
            # bueno deja de ser representativo en cuanto cambian las reglas de
            # ejecución bajo las que se entrenó, y sus métricas no lo delatan.
            # Sólo alerta y sugiere reentrenar: nunca desactiva ni autoactiva.
            # El aviso lleva latch (una vez por situación, no por vuelta).
            rules_cfg = self._settings.ml.execution_rules_check
            if rules_cfg.enabled:
                rules_interval = max(60.0, rules_cfg.check_interval_seconds)

                async def ml_execution_rules_check() -> None:
                    await ml_engine.run_execution_rules_check()

                scheduler.add_job(
                    "ml_execution_rules_check",
                    ml_execution_rules_check,
                    interval_seconds=rules_interval,
                    run_immediately=True,
                )
            scheduler.add_job(
                "ml_meta_evaluation", ml_meta_evaluation, interval_seconds=meta_interval
            )

        # Quant Research Lab (Fase 10): ciclo autónomo de generación. El
        # laboratorio existía pero nada lo disparaba — sólo se registraba su
        # notificador —, así que `experiments` se quedaba en 0 para siempre.
        # El ciclo **descubre y valida candidatas sobre copias**: no opera, no
        # promueve y no habilita live; la promoción sigue exigiendo aprobación
        # humana. Está detrás de `auto_cycle` porque los backtests compiten por
        # CPU con el motor que está operando.
        # `auto_cycle` sigue en False por defecto: activarlo es una decisión
        # explícita del operador tras ver la propuesta de presupuesto
        # (`docs/research.md`). El presupuesto en sí (ventana, topes, timeout)
        # ya está aplicado en `_run_research_cycle`.
        if self._container.contains(ResearchLab) and self._settings.research.auto_cycle:
            research = self._container.resolve(ResearchLab)
            research_interval = max(3_600.0, self._settings.research.cycle_interval_seconds)

            async def research_cycle() -> None:
                await self._run_research_cycle(research)

            scheduler.add_job("research_cycle", research_cycle, interval_seconds=research_interval)

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

    async def _run_research_cycle(self, research: ResearchLab) -> None:
        """Run one autonomous generation cycle per configured symbol.

        Trabaja **sobre copias**: genera genomas, los valida en el cluster de
        simulación contra histórico y registra las que califican. Nunca envía
        órdenes ni promueve nada — la promoción exige aprobación humana.

        Un símbolo sin histórico suficiente se salta con un aviso; un fallo en
        uno no puede impedir que se procesen los demás.

        Corre bajo un **presupuesto de CPU explícito** (ventana horaria, tope de
        símbolos y genomas, timeout duro): comparte VPS con el motor que está
        operando, y el bucle de gestión de posiciones es el único que no puede
        llegar tarde.
        """
        settings = self._settings.research
        cpu_pct = self._current_cpu_pct()
        # Antes del presupuesto: si el motor operativo se está degradando, el
        # ciclo no se pospone — se **apaga**. Posponer dejaría el problema vivo
        # para el disparo siguiente, y de madrugada nadie lo estaría mirando.
        if await self._research_rollback_fired(cpu_pct):
            return
        decision = evaluate_budget(
            settings.budget,
            cpu_pct=cpu_pct,
            open_positions=self._open_position_count(),
        )
        if not decision.allowed:
            self._log.info("Ciclo de research pospuesto: %s", decision.reason)
            return

        symbols = settings.cycle_symbols or list(self._settings.market.symbols)
        if not symbols:
            self._log.warning("Ciclo de research sin símbolos configurados; se omite")
            return
        # El tope de símbolos acota el trabajo total por ejecución; el de
        # genomas acota la población del generador, que es la variable que más
        # multiplica el número de backtests.
        symbols = symbols[: decision.max_symbols]

        try:
            # Timeout DURO sobre el ciclo completo: un ciclo colgado no puede
            # quedarse consumiendo CPU hasta el siguiente disparo.
            await asyncio.wait_for(
                self._research_cycle_body(research, symbols, decision.max_generated),
                timeout=decision.timeout_seconds,
            )
        except TimeoutError:
            self._log.warning(
                "Ciclo de research cancelado por timeout (%.0fs). Los candidatos ya "
                "registrados se conservan; el resto se retomará en el próximo ciclo.",
                decision.timeout_seconds,
            )

    async def _research_cycle_body(
        self, research: ResearchLab, symbols: list[str], max_generated: int
    ) -> None:
        """Generate and validate one batch per symbol (bajo el timeout duro)."""
        settings = self._settings.research
        market = self._container.resolve(MarketDataService)
        timeframe = Timeframe(settings.cycle_timeframe)
        for raw_symbol in symbols:
            symbol = raw_symbol.upper()
            candles = market.get_candles(symbol, timeframe, limit=settings.cycle_candles)
            if len(candles) < 100:
                self._log.info(
                    "Research: %s sin histórico suficiente (%d velas); se omite",
                    symbol,
                    len(candles),
                )
                continue
            try:
                summary = await research.run_generation_cycle(
                    symbol,
                    timeframe.value,
                    candles,
                    research.make_config(symbol),
                    count=max_generated,
                )
            except Exception:
                # El laboratorio nunca puede tumbar el motor que está operando.
                self._log.exception("Ciclo de research falló en %s", symbol)
                continue
            self._log.info(
                "Research %s: %d generadas, %d calificadas",
                symbol,
                summary.get("generated", 0),
                summary.get("qualified", 0),
            )

    def _watch_pipeline_alarms(self) -> None:
        """Feed the ciego/mudo alarms into the research rollback monitor.

        El motor ciego o mudo es la degradación más grave que hay. No hace falta
        demostrar que la causó el research: con el motor sin operar, apagar el
        laboratorio no cuesta nada y puede ser justo lo que hacía falta.
        """
        bus = self._container.resolve(EventBus)
        monitor = self._research_rollback

        async def on_pipeline_event(event: Event) -> None:
            if isinstance(event, MarketDataBlind):
                monitor.on_pipeline_alarm("market_data_blind")
            elif isinstance(event, MarketDataRecovered):
                monitor.on_pipeline_recovered("market_data_blind")
            elif isinstance(event, SignalDrought):
                monitor.on_pipeline_alarm("signal_drought")

        for event_type in (MarketDataBlind, MarketDataRecovered, SignalDrought):
            bus.subscribe(on_pipeline_event, event_type)

    async def _research_rollback_fired(self, cpu_pct: float | None) -> bool:
        """Disable ``auto_cycle`` if the operating engine is degrading.

        Args:
            cpu_pct: Lectura de CPU ya obtenida (``None`` = sensor mudo).

        Returns:
            Si se desactivó el ciclo en esta pasada.
        """
        monitor = self._research_rollback
        if monitor is None or not self._settings.research.rollback.enabled:
            return False

        latency = self._manage_latency()
        triggers = monitor.evaluate(
            cpu_pct=cpu_pct,
            manage_ema_seconds=latency["ema_seconds"],
            baseline_latency_seconds=self._manage_latency_baseline,
            manage_passes=latency["passes"],
        )
        if not triggers:
            # Sin degradación, la referencia se refresca: la línea base es lo
            # que el bucle tarda cuando el sistema está sano, no un valor fijo.
            ema = latency["ema_seconds"]
            if (
                ema is not None
                and latency["passes"] >= self._settings.research.rollback.min_manage_passes
            ):
                self._manage_latency_baseline = ema
            return False

        # El toggle en memoria basta: el job lee `auto_cycle` en cada disparo y
        # el scheduler ya no volverá a entrar aquí. Persistirlo en `.env` sería
        # que el código se modifique a sí mismo la configuración del operador.
        self._settings.research.auto_cycle = False
        detail = "; ".join(f"{t.name}: {t.detail}" for t in triggers)
        self._log.critical("Ciclo de research desactivado automáticamente — %s", detail)
        await self._container.resolve(EventBus).publish(
            ResearchCycleRolledBack(
                source="engine",
                triggers=tuple(t.name for t in triggers),
                detail=detail,
            )
        )
        return True

    def _manage_latency(self) -> dict[str, Any]:
        """Latencia del bucle de gestión de posiciones (vacía si no hay ejecución)."""
        if not self._container.contains(ExecutionCore):
            return {"passes": 0, "last_seconds": None, "ema_seconds": None}
        return dict(self._container.resolve(ExecutionCore).engine.manage_latency)

    def _current_cpu_pct(self) -> float | None:
        """Host CPU usage, or ``None`` when it cannot be measured.

        Un sensor mudo no bloquea el laboratorio, igual que Safe Mode no degrada
        la operativa por una lectura que falta.
        """
        if not self._container.contains(HealthMonitor):
            return None
        snapshot = self._container.resolve(HealthMonitor).last_snapshot
        return None if snapshot is None else snapshot.cpu_percent

    def _open_position_count(self) -> int:
        """Open positions right now (0 if the execution layer is not wired)."""
        if not self._container.contains(ExecutionCore):
            return 0
        return len(self._container.resolve(ExecutionCore).positions.open_positions)

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

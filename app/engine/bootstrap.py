"""Composition root: aquí (y solo aquí) se construyen e inyectan los módulos.

Ningún módulo importa implementaciones de otro módulo: reciben sus
dependencias por constructor. Este archivo es el único que conoce el grafo.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from app.backtesting import BacktestLab
from app.brokers.mt5.connection import MT5Connection, MT5ConnectionConfig
from app.cache.memory import InMemoryCache
from app.cache.redis_backend import RedisCache
from app.cache.service import CacheService
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.dashboard.api.service import ApiService
from app.database.engine import DatabaseManager
from app.documentation.backends import MarkdownJournalBackend, NotionJournalBackend
from app.documentation.service import DocumentationService
from app.engine.attribution import EdgeAttributionEngine, FactorCapture, FactorSnapshotStore
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.correlation import CorrelationEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.edge_research import EdgeReportHistory, EdgeResearchEngine
from app.engine.evaluation import PerformanceTracker, VirtualOutcomeStore
from app.engine.feature_store import FeatureStore
from app.engine.filters import build_filter_chain
from app.engine.market_context import MarketContextEngine
from app.engine.meta_governance import MetaGovernanceApplier
from app.engine.microstructure import MicrostructureEngine
from app.engine.microstructure.features import register_microstructure_features
from app.engine.models import SignalRecord
from app.engine.plugins import PluginLoader
from app.engine.position_quality import PositionQualityEngine
from app.engine.quant_core import QuantCore
from app.engine.regime_detection import RegimeDetector
from app.engine.regime_forecast import RegimeForecastEngine, RegimeForecastService
from app.engine.rejections import RejectionStore
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import HistoryWriter, SignalHistoryStore
from app.engine.strategy_engine import StrategyEngine
from app.engine.trade_explain import SignalVerdict as ExplainVerdict
from app.engine.trade_explain import TradeExplainer
from app.engine.validators import SignalValidator
from app.execution.api import ExecutionCore
from app.execution.benchmark import ShadowBenchmark
from app.execution.commission import CommissionEngine
from app.execution.costs import CostAttributionEngine
from app.execution.execution_engine import ExecutionEngine
from app.execution.falsification import HoldingChangeFalsifier
from app.execution.journal import TradeJournal
from app.execution.latency import LatencyEngine
from app.execution.notifications import ExecutionNotifier
from app.execution.optimizer import ExecutionOptimizer
from app.execution.order_manager import OrderManager
from app.execution.performance import PerformanceEngine
from app.execution.portfolio_manager import PortfolioManager
from app.execution.position_manager import PositionManager
from app.execution.risk_manager import RiskManager
from app.execution.sizing import PositionSizer
from app.execution.slippage import SlippageEngine
from app.execution.strategy_experiments import StrategyExperimentManager
from app.market.aggregator import CandleAggregator
from app.market.cache import MarketCache
from app.market.collector import TickCollector
from app.market.feed import MarketFeed
from app.market.models import Timeframe
from app.market.providers.mt5 import MT5MarketProvider
from app.market.providers.registry import ProviderRegistry
from app.market.services import MarketDataService, MarketStateStore, OrderBookManager
from app.market.storage import MarketDataWriter
from app.market.stream import FeedMetrics, WebSocketManager
from app.market.validator import DataValidator
from app.ml.api import MLEngine
from app.ml.datasets import SignalOutcome
from app.ml.notifications import MLNotifier
from app.ml.services import EdgeHealthStats, VirtualStrategyStats
from app.monitoring.data_quality import DataQualityEngine, DataQualityInputs
from app.monitoring.data_quality_service import DataQualityMonitor
from app.monitoring.health import HealthMonitor
from app.monitoring.meta_risk import MetaRiskEngine, MetaRiskInputs
from app.monitoring.pipeline_watch import PipelineWatchdog
from app.monitoring.watchdog import Watchdog
from app.notifications.channels.discord import DiscordWebhookChannel
from app.notifications.channels.discord_router import build_routed_discord
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService
from app.portfolio import PortfolioIntelligence
from app.production.api import ProductionAPI
from app.production.audit import AuditLog, audit_log
from app.production.backup import BackupService
from app.production.failover import FailoverCoordinator
from app.production.improvement import ContinuousImprovementEngine, ImprovementService
from app.production.kill_switch import KillSwitchController
from app.production.licenses import LicenseManager
from app.production.live import ApprovalStore, LiveGate, ModeResolver, select_broker
from app.production.maintenance import MaintenanceManager
from app.production.recovery import RecoveryService, StateSnapshotStore
from app.production.reporting import ReportService
from app.production.safe_mode import SafeModeController
from app.production.updates import UpdateManager
from app.research.api import ResearchLab
from app.research.notifications import ResearchNotifier
from app.scheduler.scheduler import AsyncScheduler
from app.security.secrets import SecretRotationManager

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_container(settings: Settings) -> Container:
    """Wire every Phase-1 service into a DI container.

    Args:
        settings: Central configuration for the active environment.

    Returns:
        Container with singletons for each service.
    """
    container = Container()
    container.register_instance(Settings, settings)

    # --- Event Bus -----------------------------------------------------
    bus = EventBus()
    container.register_instance(EventBus, bus)

    # --- Cache (Redis primario + memoria como fallback) ----------------
    primary = RedisCache(
        settings.cache.url,
        socket_timeout_seconds=settings.cache.socket_timeout_seconds,
    )
    cache = CacheService(
        primary=primary,
        fallback=InMemoryCache(),
        retry_cooldown_seconds=settings.cache.retry_cooldown_seconds,
    )
    container.register_instance(CacheService, cache)

    # --- Base de datos (perezosa: sin conexión hasta el primer uso) ----
    container.register_instance(DatabaseManager, DatabaseManager(settings.database))

    # --- Terminal MetaTrader 5 (modo demo) — conexión compartida broker+feed --
    # Se construye antes que el Data Engine y la Ejecución para que ambos usen el
    # MISMO terminal y el mismo lock. Sólo en modo demo con broker MT5 configurado.
    _maybe_build_mt5(container, settings)

    # --- Notificaciones (regla global: solo Discord) --------------------
    try:
        min_level = NotificationLevel(settings.discord.min_level.lower())
    except ValueError:
        min_level = NotificationLevel.INFO
    notifications = NotificationService(min_level=min_level, enabled=settings.discord.enabled)
    webhook = settings.discord.webhook_url.get_secret_value()
    if settings.discord.enabled and webhook:
        logical = {name: url.get_secret_value() for name, url in settings.discord.channels.items()}
        if logical:
            # Fase 9: canales lógicos con webhooks propios (routing por fuente).
            notifications.register_channel(
                build_routed_discord(
                    webhook,
                    logical,
                    routing=settings.discord.routing,
                    timeout_seconds=settings.discord.timeout_seconds,
                    max_retries=settings.discord.max_retries,
                )
            )
        else:
            notifications.register_channel(
                DiscordWebhookChannel(
                    webhook,
                    timeout_seconds=settings.discord.timeout_seconds,
                    max_retries=settings.discord.max_retries,
                )
            )
    container.register_instance(NotificationService, notifications)

    # --- Scheduler / Watchdog / Health ----------------------------------
    scheduler = AsyncScheduler()
    container.register_instance(AsyncScheduler, scheduler)

    watchdog = Watchdog(settings.watchdog, bus)
    container.register_instance(Watchdog, watchdog)

    health = HealthMonitor(settings.health, bus, watchdog)
    container.register_instance(HealthMonitor, health)

    # --- Documentación (bitácora Markdown + Notion con cola, Fase 9) -----
    documentation = DocumentationService()
    documentation.register_backend(MarkdownJournalBackend(_PROJECT_ROOT / "docs" / "bitacora.md"))
    notion = settings.notion
    if notion.enabled and notion.api_key.get_secret_value() and notion.database_id:
        documentation.register_backend(
            NotionJournalBackend(
                notion.api_key.get_secret_value(),
                notion.database_id,
                version=notion.version,
                timeout_seconds=notion.timeout_seconds,
                max_retries=notion.max_retries,
                queue_path=notion.queue_path,
            )
        )
    container.register_instance(DocumentationService, documentation)

    # --- Overrides del Config Center -------------------------------------
    # DEBE ir antes de construir los subsistemas: muchos leen su configuración
    # al construirse y la copian a atributos propios (p. ej. el PositionManager
    # con `trailing_enabled`). Reaplicarlos después dejaba esos overrides sin
    # efecto **para siempre** — ni un reinicio los aplicaba: el dashboard decía
    # "saved" y `GET /api/config` los marcaba `overridden`, pero el motor seguía
    # con el valor del `.env`.
    from app.dashboard.api.config_store import config_store

    config_store.reapply(settings)

    # --- Data Engine (Fase 2) --------------------------------------------
    if settings.market.enabled:
        _build_market(container, settings, bus, cache)

    # --- Quant Core (Fase 3) — requiere el Data Engine -------------------
    if settings.quant.enabled and settings.market.enabled:
        _build_quant(container, settings, bus)

    # --- Execution Engine (Fase 5) — paper trading, requiere el Data Engine
    if settings.execution.enabled and settings.market.enabled:
        _build_execution(container, settings, bus)

    # --- Machine Learning (Fase 7) — asesora, nunca decide ni opera -------
    if settings.ml.enabled:
        _build_ml(container, settings, bus)

    # --- Laboratorio de backtesting (Fase 6) — bajo demanda, nunca live --
    if settings.backtesting.enabled:
        container.register_instance(BacktestLab, BacktestLab(settings))

    # --- Quant Research Lab (Fase 10) — investiga, nunca opera --------------
    # Independiente de producción: descubre y valida estrategias sobre copias y
    # jamás habilita live trading. Reutiliza el laboratorio de backtesting.
    if settings.research.enabled:
        _build_research(container, settings, bus)

    # --- Producción (Fase 9) — live gating, safe mode, kill switch --------
    # Va después de ejecución y ML porque los observa a todos. Live sigue
    # deshabilitado: esto construye el gate, no lo abre.
    if settings.production.enabled:
        _build_production(container, settings, bus)

    # --- API del dashboard ----------------------------------------------
    api_app = create_app(settings, container)
    container.register_instance(ApiService, ApiService(api_app, settings.dashboard))

    return container


def _wants_mt5(settings: Settings) -> bool:
    """Whether the demo MT5 broker/feed should be wired.

    Se activa cuando el modo de ejecución es ``demo`` o el broker configurado es
    ``mt5_exness``. Ambos convergen en el mismo terminal MT5.
    """
    return settings.execution.mode == "demo" or settings.broker.name == "mt5_exness"


def _maybe_build_mt5(container: Container, settings: Settings) -> None:
    """Register a shared :class:`MT5Connection` when demo mode is configured.

    No conecta el terminal aquí (eso ocurre al arrancar el feed): sólo construye
    y registra la conexión para que broker y feed compartan un único terminal.
    """
    if not _wants_mt5(settings):
        return
    mt5 = settings.broker.mt5
    if mt5.login <= 0 or not mt5.server:
        # Config incompleta: se registra igualmente para que select_broker dé un
        # error claro y accionable en vez de un fallo opaco más adelante.
        pass
    config = MT5ConnectionConfig(
        login=mt5.login,
        password=mt5.password.get_secret_value(),
        server=mt5.server,
        terminal_path=mt5.terminal_path or None,
        timeout_ms=mt5.timeout_ms,
        portable=mt5.portable,
    )
    container.register_instance(MT5Connection, MT5Connection(config))


def _resolve_initial_balance(execution: object, mt5_conn: MT5Connection | None) -> float:
    """Return the seed balance for the accounting layer.

    En modo ``demo`` con ``use_broker_balance`` y una conexión MT5, se lee el
    balance REAL de la cuenta (el operador lo recarga en Exness). Si el terminal
    no responde, se cae al ``initial_balance`` del ``.env``. En paper siempre se
    usa ``initial_balance``.

    Args:
        execution: ``ExecutionSettings`` (tipado laxo para evitar el import cíclico).
        mt5_conn: Conexión MT5 compartida, o ``None``.

    Returns:
        El balance semilla.
    """
    log = logging.getLogger("app.engine.bootstrap")
    fallback = float(getattr(execution, "initial_balance", 10_000.0))
    is_demo = getattr(execution, "resolved_mode", lambda: "paper")() == "demo"
    if not (is_demo and getattr(execution, "use_broker_balance", False) and mt5_conn is not None):
        return fallback
    try:
        mt5_conn.connect()
        balance = mt5_conn.account_balance()
    except Exception as exc:  # BrokerConnectionError u otros: no romper el arranque
        log.warning("No se pudo leer el balance de la cuenta demo (%s); uso %.2f", exc, fallback)
        return fallback
    if balance is None or balance <= 0:
        log.warning("Balance de la cuenta demo no disponible; uso fallback %.2f", fallback)
        return fallback
    log.info("Balance semilla leído de la cuenta demo MT5: %.2f", balance)
    return balance


def _build_market(
    container: Container, settings: Settings, bus: EventBus, cache: CacheService
) -> None:
    """Wire the Data Engine (validador → pipeline → feed → servicio).

    Args:
        container: Contenedor DI en construcción.
        settings: Configuración central.
        bus: Event Bus ya registrado.
        cache: Servicio de cache ya registrado.
    """
    market = settings.market
    quality = market.quality
    validator = DataValidator(
        max_price_jump_pct=quality.max_price_jump_pct,
        max_volume=quality.max_volume,
        future_tolerance_seconds=quality.future_tolerance_seconds,
        stale_seconds=quality.stale_data_seconds,
        duplicate_window=quality.duplicate_window,
        out_of_order_grace=quality.out_of_order_grace_seconds,
    )
    container.register_instance(DataValidator, validator)
    timeframes = []
    for name in market.timeframes:
        try:
            timeframes.append(Timeframe(name))
        except ValueError:
            continue  # timeframe desconocido: se ignora (queda logueado en docs)
    aggregator = CandleAggregator(timeframes or [Timeframe.M1])
    books = OrderBookManager(max_depth=market.orderbook_depth * 4)
    # Microstructure Engine (Bloque 3): observa los deltas del libro y las
    # operaciones en el mismo punto en que se aplican. Se construye aquí, con
    # el Data Engine, porque su fuente es el feed — no la decisión.
    microstructure = MicrostructureEngine(settings.quant.microstructure)
    container.register_instance(MicrostructureEngine, microstructure)
    state = MarketStateStore(
        recent_trades_limit=market.recent_trades_limit,
        candle_history_limit=market.candle_history_limit,
    )
    market_cache = MarketCache(cache)
    writer = MarketDataWriter(container.resolve(DatabaseManager), market.storage)
    container.register_instance(MarketDataWriter, writer)

    collector = TickCollector(
        bus=bus,
        validator=validator,
        aggregator=aggregator,
        books=books,
        state=state,
        cache=market_cache,
        writer=writer,
        metrics=FeedMetrics(),
        queue_size=market.collector_queue_size,
        aggregate_from_ticks=market.aggregate_from_ticks,
        book_observer=microstructure,
    )
    container.register_instance(TickCollector, collector)

    # Data Quality Engine (Bloque 11): mide la salud del dato y la traduce en un
    # multiplicador de exposicion. La leccion del incidente del 04/08 es que el
    # motor puede quedarse ciego sin un solo error en el log; esto lo mide.
    metrics = collector.metrics
    quality_engine = DataQualityEngine(
        settings.data_quality,
        lambda: _data_quality_inputs(metrics, books, state, list(market.symbols)),
    )
    container.register_instance(DataQualityEngine, quality_engine)

    # Meta Risk Engine (Bloque 12): la salud de la maquina. Compone con la
    # calidad del dato y produce el multiplicador que consume la ejecucion.
    health = container.resolve(HealthMonitor) if container.contains(HealthMonitor) else None
    watchdog = container.resolve(Watchdog) if container.contains(Watchdog) else None
    meta_risk = MetaRiskEngine(
        settings.meta_risk,
        lambda: _meta_risk_inputs(health, watchdog, quality_engine),
    )
    container.register_instance(MetaRiskEngine, meta_risk)
    container.register_instance(
        DataQualityMonitor,
        DataQualityMonitor(settings.data_quality, quality_engine, bus, meta_risk),
    )

    ws_manager = WebSocketManager()
    container.register_instance(WebSocketManager, ws_manager)

    registry = ProviderRegistry(market)
    # Si hay terminal MT5 compartido, el proveedor 'mt5' deja de ser un stub y usa
    # la conexión real (mismo terminal y lock que el broker de ejecución demo).
    if container.contains(MT5Connection):
        connection = container.resolve(MT5Connection)
        poll = settings.broker.mt5.tick_poll_seconds
        registry.register("mt5", lambda _s: MT5MarketProvider(connection, poll_seconds=poll))
    container.register_instance(ProviderRegistry, registry)

    feed = MarketFeed(market, registry, collector, bus, ws_manager)
    container.register_instance(MarketFeed, feed)

    container.register_instance(MarketDataService, MarketDataService(state, market_cache, feed))
    container.register_instance(MarketStateStore, state)
    container.register_instance(MarketCache, market_cache)


def _build_quant(container: Container, settings: Settings, bus: EventBus) -> None:
    """Wire the Quant Core (Fase 3): estrategias → señales → decisión.

    Args:
        container: Contenedor DI en construcción.
        settings: Configuración central.
        bus: Event Bus ya registrado.
    """
    quant = settings.quant
    market_service = container.resolve(MarketDataService)
    scheduler = container.resolve(AsyncScheduler)

    features = FeatureStore(market_service)
    # Bloque 3: las métricas de microestructura entran al Feature Store desde
    # fuera, para que ni el store conozca el motor ni el motor conozca al store.
    if container.contains(MicrostructureEngine):
        register_microstructure_features(features, container.resolve(MicrostructureEngine))
    container.register_instance(FeatureStore, features)

    regime = RegimeDetector(market_service, quant.regime)
    # Regime Forecast Engine (Bloque 4): pronostica el proximo desenlace desde
    # la frecuencia condicional empirica, y se puntua a si mismo contra el
    # pronostico trivial. No decide nada: publica probabilidad y skill.
    # Correlation Intelligence (Bloque 5): mide lo que los grupos manuales del
    # filtro de correlacion no pueden saber — que dos simbolos han dejado de
    # moverse juntos, o han empezado. Se une a la regla manual, no la sustituye.
    correlation_engine = CorrelationEngine(
        quant.correlation,
        market_service,
        list(settings.market.symbols),
        timeframe=Timeframe(quant.context.context_timeframe),
    )
    container.register_instance(CorrelationEngine, correlation_engine)

    forecast_engine = RegimeForecastEngine(quant.regime_forecast)
    container.register_instance(RegimeForecastEngine, forecast_engine)
    container.register_instance(
        RegimeForecastService,
        RegimeForecastService(
            quant.regime_forecast,
            forecast_engine,
            regime,
            market_service,
            list(settings.market.symbols),
            timeframe=Timeframe(quant.context.context_timeframe),
            bus=bus,
        ),
    )
    context_engine = MarketContextEngine(market_service, features, regime, quant.context)
    container.register_instance(MarketContextEngine, context_engine)

    history_writer = HistoryWriter(container.resolve(DatabaseManager), quant.history)
    container.register_instance(HistoryWriter, history_writer)

    # Evaluación continua (Fase 4): resultado virtual de cada señal.
    # El store (Bloque 8) conserva ese resultado fila a fila, indexado por
    # `signal_id`, que es lo que permite unirlo después con el Trade Journal.
    outcomes = VirtualOutcomeStore(
        quant.evaluation.outcomes_path,
        persist=quant.evaluation.persist_outcomes,
        flush_size=quant.evaluation.outcomes_flush_size,
    )
    container.register_instance(VirtualOutcomeStore, outcomes)
    tracker = PerformanceTracker(quant.evaluation, market_service, outcomes)
    container.register_instance(PerformanceTracker, tracker)

    # Edge Research Engine (Bloque 1): salud del edge por estrategia sobre una
    # ventana rodante. Lee las resoluciones ya escritas por el evaluador — se le
    # inyecta el `load` del store, no el store, para que el mismo motor sirva
    # sobre el histórico de un backtest sin arrastrar servicios. No opera, no
    # cambia pesos y no puede habilitar live: sólo produce evidencia.
    edge_history = EdgeReportHistory(
        quant.edge_research.history_path,
        persist=quant.edge_research.persist_history,
        memory_limit=quant.edge_research.history_memory_limit,
    )
    container.register_instance(EdgeReportHistory, edge_history)
    edge_research = EdgeResearchEngine(
        quant.edge_research,
        outcomes.load,
        edge_history,
        bus,
    )
    container.register_instance(EdgeResearchEngine, edge_research)

    # Captura de factores (Bloque 2): fotografía el estado de order flow, VWAP,
    # momentum, delta/CVD y el desglose de confianza en el momento de decidir.
    # Se engancha al bus, NO al Decision Engine: el motor de decisión está en el
    # camino caliente y no puede pagar lecturas extra por evaluación. El precio
    # es un desfase de hasta el TTL del Feature Store, medido en cada fila.
    snapshots = FactorSnapshotStore(
        quant.attribution.snapshots_path,
        persist=quant.attribution.persist_snapshots,
        flush_size=quant.attribution.snapshots_flush_size,
    )
    container.register_instance(FactorSnapshotStore, snapshots)

    # El sink reparte cada señal resuelta a persistencia batched Y al
    # evaluador continuo: historial en memoria, DB y métricas son un flujo.
    def _signal_sink(record: SignalRecord) -> None:
        history_writer.add_signal(record)
        tracker.on_signal_record(record)

    history = SignalHistoryStore(
        memory_limit=quant.history.memory_limit,
        signal_sink=_signal_sink,
    )
    container.register_instance(SignalHistoryStore, history)

    capture = FactorCapture(features, history.decisions, snapshots, bus)
    container.register_instance(FactorCapture, capture)

    weights = {name: config.weight for name, config in quant.strategies.items()}
    consensus = ConsensusEngine(
        quant.consensus, weights, performance_factor=history.performance_factor
    )
    confidence = ConfidenceEngine(quant.confidence)
    # El filtro de microestructura es fail-open: sin libro del proveedor no
    # mide y deja pasar. Se cablea sólo si el motor existe, para que en su
    # ausencia ni siquiera aparezca en la explicación de la decisión.
    micro_reader = (
        (
            quant.microstructure.max_execution_pressure,
            _execution_pressure(container.resolve(MicrostructureEngine)),
        )
        if container.contains(MicrostructureEngine)
        else None
    )
    quality_engine = PositionQualityEngine(quant.position_quality)
    container.register_instance(PositionQualityEngine, quality_engine)

    filters = build_filter_chain(
        quant.filters,
        # El override del operador (`execution.risk.ignore_drawdown_limits`) se
        # lee en cada evaluación, no al construir la cadena: así el toggle del
        # dashboard aplica en caliente. Con él activo el filtro ve 0% y nunca
        # bloquea, en vez de sacarlo de la cadena (seguiría apareciendo en la
        # explicación de la decisión, que es lo que queremos auditar).
        drawdown_reader=lambda: (
            0.0
            if settings.execution.risk.ignore_drawdown_limits
            else history.get_state("daily_drawdown_pct")
        ),
        recent_decisions=lambda: history.decisions(limit=50),
        microstructure=micro_reader,
        measured_correlation=(
            correlation_engine.correlated_with if quant.correlation.enabled else None
        ),
        # El lector de entradas del veto de calidad queda sin cablear todavia:
        # el coste esperado vive en la capa de ejecucion (Bloque 6) y el riesgo
        # propuesto en el Risk Manager, y ninguno se conoce en el momento en que
        # corre la cadena de filtros. Sin lector, esas dimensiones quedan como
        # NO observables y el filtro no bloquea por ellas — que es exactamente
        # el comportamiento correcto, no una degradacion silenciosa.
        position_quality=(quality_engine, None) if quant.position_quality.enabled else None,
    )

    # Why Not Trade Engine (Bloque 14): convierte la explicacion en texto del
    # rechazo en datos agregables. Se inyecta en el Decision Engine porque el
    # resultado de cada filtro y el deficit de cada umbral solo existen ahi.
    rejections = RejectionStore(quant.rejections)
    container.register_instance(RejectionStore, rejections)

    validator = SignalValidator()
    signal_engine = SignalEngine(
        validator,
        history,
        bus,
        signal_ttl_seconds=quant.signal_ttl_seconds,
        dedupe_window_seconds=quant.dedupe_window_seconds,
    )
    container.register_instance(SignalEngine, signal_engine)

    decision_engine = DecisionEngine(
        signal_engine,
        context_engine,
        consensus,
        confidence,
        filters,
        history,
        quant.consensus,
        bus,
        history_writer,
        rejections,
    )
    container.register_instance(DecisionEngine, decision_engine)

    loader = PluginLoader(list(quant.plugin_dirs))
    from app.dashboard.api.config_store import config_store

    strategy_engine = StrategyEngine(
        quant,
        loader,
        market_service,
        features,
        context_engine,
        signal_engine,
        decision_engine,
        bus,
        scheduler,
        overrides=config_store.strategy_overrides,
    )
    container.register_instance(StrategyEngine, strategy_engine)

    # Vigilancia del pipeline: el motor puede dejar de operar sin caerse —ciego
    # (descarta los datos) o mudo (no emite señales)— y ninguna comprobación de
    # salud convencional lo nota. Lee contadores ya existentes; no toca el
    # camino caliente de los datos.
    if settings.pipeline_watch.enabled and container.contains(DataValidator):
        data_validator = container.resolve(DataValidator)
        container.register_instance(
            PipelineWatchdog,
            PipelineWatchdog(
                settings.pipeline_watch,
                bus,
                stats_provider=lambda: data_validator.stats,
                signal_count_provider=lambda: sum(
                    s.signals_produced for s in strategy_engine.stats()
                ),
                notifications=container.resolve(NotificationService),
            ),
        )

    container.register_instance(
        QuantCore,
        QuantCore(
            strategies=strategy_engine,
            signals=signal_engine,
            decisions=decision_engine,
            consensus=consensus,
            confidence=confidence,
            context=context_engine,
            regime=regime,
            filters=filters,
            features=features,
            history=history,
            loader=loader,
            writer=history_writer,
            performance=tracker,
        ),
    )


def _build_execution(container: Container, settings: Settings, bus: EventBus) -> None:
    """Wire the Execution Engine (Fase 5): riesgo → broker → cartera → journal.

    Regla de oro: sólo paper trading. Desde la Fase 9 la elección del broker es
    una decisión explícita (``select_broker``) en vez de una construcción
    incondicional, pero sigue teniendo una sola salida posible: si el modo
    resuelto no fuera ``paper``, el motor no arranca.

    Args:
        container: Contenedor DI en construcción.
        settings: Configuración central.
        bus: Event Bus ya registrado.
    """
    execution = settings.execution
    market_service = container.resolve(MarketDataService)

    commission = CommissionEngine(execution.commission)
    slippage = SlippageEngine(execution.slippage)
    latency = LatencyEngine(execution.latency)
    # El modo aún no está resuelto por el ModeResolver (la capa de producción se
    # construye después, y necesita el motor de ejecución). Se usa el suelo
    # estático de settings: 'paper', o 'demo' si hay broker MT5 configurado.
    mt5_conn = container.resolve(MT5Connection) if container.contains(MT5Connection) else None
    broker = select_broker(
        execution.resolved_mode(),
        execution,
        commission,
        slippage,
        latency,
        mt5_connection=mt5_conn,
        deviation_points=settings.broker.mt5.deviation_points,
        magic=settings.broker.mt5.magic,
    )

    # Balance semilla: en modo demo se lee el balance REAL de la cuenta MT5 (que
    # el operador puede recargar en Exness); en paper, o si el terminal no
    # responde, se usa el valor fijo del .env como fallback.
    initial_balance = _resolve_initial_balance(execution, mt5_conn)

    sizer = PositionSizer(execution.sizing)
    positions = PositionManager(
        break_even_r=execution.break_even_r,
        trailing_enabled=execution.trailing_enabled,
        trailing_atr_multiple=execution.trailing_atr_multiple,
        trailing_activate_r=execution.trailing_activate_r,
    )
    portfolio = PortfolioManager(
        initial_balance,
        base_currency=execution.base_currency,
        leverage=execution.leverage,
    )
    risk = RiskManager(execution.risk, initial_balance)
    orders = OrderManager()
    journal = TradeJournal(
        execution.journal_path if execution.persist_journal else None,
        persist=execution.persist_journal,
    )
    container.register_instance(TradeJournal, journal)

    # Execution Optimizer (Bloque 6): compara IOC/LIMIT/MARKET antes de mandar
    # nada. Reutiliza los motores de slippage y latencia de la Fase 5 en vez de
    # duplicar el modelo — si optimizar y simular usaran modelos distintos, el
    # optimizador estaria eligiendo para un mercado que el simulador no vive.
    # Cost Attribution Engine (Bloque 9): separa el bruto de todo lo que se lo
    # come. El coste de oportunidad se mide con las senales que nunca llegaron a
    # operacion, asi que consume el store de resultados virtuales si existe.
    outcome_store = (
        container.resolve(VirtualOutcomeStore) if container.contains(VirtualOutcomeStore) else None
    )
    container.register_instance(
        CostAttributionEngine,
        CostAttributionEngine(
            settings.costs,
            journal.all,
            (lambda: list(outcome_store.load())) if outcome_store is not None else None,
        ),
    )

    # Live Shadow Benchmark (Bloque 15): compara paper contra el fill ideal y
    # deja el carril live DECLARADO como ausente. No recibe ningun proveedor de
    # operaciones live y no conoce ningun broker: no puede habilitar nada.
    cost_engine = container.resolve(CostAttributionEngine)
    container.register_instance(
        ShadowBenchmark,
        ShadowBenchmark(
            settings.shadow_benchmark,
            journal.all,
            None,
            lambda: cost_engine.analyze().total.opportunity,
        ),
    )

    # Portfolio Intelligence (Bloque 8): desglosa el PnL realizado por simbolo,
    # estrategia, sesion y regimen. Lee del Trade Journal, asi que se cablea
    # aqui, donde el journal acaba de construirse.
    container.register_instance(
        PortfolioIntelligence, PortfolioIntelligence(settings.portfolio, journal.all)
    )

    optimizer = ExecutionOptimizer(execution.optimizer, slippage, latency)
    container.register_instance(ExecutionOptimizer, optimizer)

    # Edge Attribution Engine (Bloque 2): une cada operación cerrada con la foto
    # de factores de su decisión. Va aquí porque necesita el Trade Journal; la
    # captura vive en el Quant Core, que es donde ocurren las decisiones.
    if container.contains(FactorSnapshotStore):
        attribution = EdgeAttributionEngine(
            settings.quant.attribution,
            journal.all,
            container.resolve(FactorSnapshotStore),
            bus,
        )
        container.register_instance(EdgeAttributionEngine, attribution)
    performance = PerformanceEngine(initial_balance)

    context_engine = (
        container.resolve(MarketContextEngine) if container.contains(MarketContextEngine) else None
    )

    # Experimentos con fecha de corte por estrategia (Bloque 2). Sólo proponen
    # desactivaciones y avisan por Discord: no tocan `strategies_enabled`.
    experiments = StrategyExperimentManager(execution.experiments)
    container.register_instance(StrategyExperimentManager, experiments)

    # Falsación del cambio de holding por estrategia (Bloque 7.1): mide sola la
    # predicción del Bloque 1 y publica el veredicto, acierte o falle.
    falsifier = HoldingChangeFalsifier(execution.falsification, execution)
    container.register_instance(HoldingChangeFalsifier, falsifier)

    engine = ExecutionEngine(
        execution,
        market_service,
        broker,
        commission,
        sizer,
        risk,
        positions,
        portfolio,
        orders,
        journal,
        performance,
        bus,
        context_engine,
        experiments,
        falsifier,
        # Bloque 11: la calidad del dato reduce exposicion. Se pasa el LECTOR,
        # no el valor: la calidad cambia en segundos y una lectura cacheada es
        # justo la que no protege.
        # Bloque 12: el multiplicador que llega a la ejecucion es el COMPUESTO
        # (infraestructura x calidad de dato). Si el Meta Risk no esta cableado
        # se cae al de calidad a secas, que sigue protegiendo.
        (
            container.resolve(MetaRiskEngine).risk_multiplier
            if container.contains(MetaRiskEngine)
            else (
                container.resolve(DataQualityEngine).risk_multiplier
                if container.contains(DataQualityEngine)
                else None
            )
        ),
    )
    container.register_instance(ExecutionEngine, engine)

    notifications = container.resolve(NotificationService)
    notifier = ExecutionNotifier(notifications, bus)
    container.register_instance(ExecutionNotifier, notifier)

    container.register_instance(
        ExecutionCore,
        ExecutionCore(
            engine=engine,
            positions=positions,
            portfolio=portfolio,
            risk=risk,
            journal=journal,
            performance=performance,
            notifier=notifier,
        ),
    )


def _build_production(container: Container, settings: Settings, bus: EventBus) -> None:
    """Wire the production layer (Fase 9): live gating, safe mode, kill switch.

    Regla absoluta: **live sigue deshabilitado**. Aquí se construye el Live Gate
    con sus criterios y su aprobación humana, pero ``allow_live`` nace en
    ``False`` y no existe camino que se salte las validaciones.

    Args:
        container: Contenedor DI en construcción.
        settings: Configuración central.
        bus: Event Bus ya registrado.
    """
    production = settings.production
    execution_core = container.resolve(ExecutionCore) if container.contains(ExecutionCore) else None
    execution_engine = (
        container.resolve(ExecutionEngine) if container.contains(ExecutionEngine) else None
    )
    risk = execution_core.risk if execution_core is not None else None

    audit = AuditLog(production.audit_path)
    container.register_instance(AuditLog, audit)

    approvals = ApprovalStore(
        production.approval_path, ttl_hours=production.live.approval_ttl_hours
    )
    container.register_instance(ApprovalStore, approvals)

    gate = LiveGate(production.live, approval_lookup=approvals.is_valid_for)
    container.register_instance(LiveGate, gate)
    container.register_instance(ModeResolver, ModeResolver(settings, gate))

    notifications = container.resolve(NotificationService)
    safe_mode = SafeModeController(production.safe_mode, bus, audit, notifications)
    container.register_instance(SafeModeController, safe_mode)
    # El Execution Engine no conoce la capa de producción: se le registra un
    # veto genérico y él lo consulta antes de cada entrada.
    if execution_engine is not None:
        execution_engine.register_entry_veto(safe_mode.entry_veto)

    kill_switch = KillSwitchController(production.kill_switch, risk, bus, audit)
    container.register_instance(KillSwitchController, kill_switch)

    store = StateSnapshotStore(production.recovery.snapshot_path)
    recovery = RecoveryService(production.recovery, store, execution_engine, bus, audit)
    container.register_instance(RecoveryService, recovery)

    # --- Operación 24/7: backups, updates, licencias, mantenimiento, HA ---
    backup = BackupService(production.backup, audit) if production.backup.enabled else None
    if backup is not None:
        container.register_instance(BackupService, backup)

    updates = (
        UpdateManager(
            production.updates,
            _PROJECT_ROOT / "app" / "database" / "migrations" / "versions",
            audit,
        )
        if production.updates.enabled
        else None
    )
    if updates is not None:
        container.register_instance(UpdateManager, updates)

    licenses = LicenseManager(production.licenses, audit)
    container.register_instance(LicenseManager, licenses)

    maintenance = (
        MaintenanceManager(production.maintenance, audit)
        if production.maintenance.enabled
        else None
    )
    if maintenance is not None:
        container.register_instance(MaintenanceManager, maintenance)

    failover = (
        FailoverCoordinator(production.failover, audit) if production.failover.enabled else None
    )
    if failover is not None:
        container.register_instance(FailoverCoordinator, failover)

    documentation = (
        container.resolve(DocumentationService)
        if container.contains(DocumentationService)
        else None
    )

    reporting = (
        ReportService(production.reporting, container, notifications, audit)
        if production.reporting.enabled
        else None
    )
    if reporting is not None:
        container.register_instance(ReportService, reporting)

    improvement = None
    if production.improvement.enabled:
        engine = ContinuousImprovementEngine(production.improvement, container)
        improvement = ImprovementService(
            engine,
            audit,
            documentation,
            report_to_docs=production.improvement.report_to_notion,
        )
        container.register_instance(ImprovementService, improvement)

    secrets = (
        SecretRotationManager(
            settings.security, production.state_dir / "secret_rotation.json", audit
        )
        if settings.security.enabled
        else None
    )
    if secrets is not None:
        container.register_instance(SecretRotationManager, secrets)

    container.register_instance(
        ProductionAPI,
        ProductionAPI(
            settings=settings,
            gate=gate,
            approvals=approvals,
            mode=container.resolve(ModeResolver),
            safe_mode=safe_mode,
            kill_switch=kill_switch,
            recovery=recovery,
            audit=audit,
            execution=execution_core,
            health=container.resolve(HealthMonitor) if container.contains(HealthMonitor) else None,
            bus=bus,
            backup=backup,
            updates=updates,
            licenses=licenses,
            maintenance=maintenance,
            failover=failover,
            reporting=reporting,
            improvement=improvement,
            documentation=documentation,
            secrets=secrets,
        ),
    )


def _build_ml(container: Container, settings: Settings, bus: EventBus) -> None:
    """Wire the Machine Learning layer (Fase 7): asesora, nunca decide ni opera.

    Regla absoluta: ningún modelo abre operaciones ni habilita live trading;
    toda recomendación pasa por el Decision Engine y el Risk Manager. El ML se
    entrena con el historial del propio motor (el Trade Journal de la Fase 5); si
    no hay ejecución cableada, se degrada con elegancia (historial vacío).

    Args:
        container: Contenedor DI en construcción.
        settings: Configuración central.
        bus: Event Bus ya registrado.
    """
    journal = container.resolve(TradeJournal) if container.contains(TradeJournal) else None
    trades_provider = journal.all if journal is not None else None

    # Segunda fuente de evidencia por estrategia: el evaluador continuo (Fase 4).
    # Mide la calidad de la señal *en sí* (TP/SL/timeout puros contra velas
    # futuras), sin sizing, trailing ni salidas por régimen. El Meta Strategy
    # Manager la usa como prior mientras la muestra ejecutada sea escasa. Se
    # adapta aquí, en el composition root, para que la capa de ML no dependa de
    # `app.engine.evaluation`.
    tracker = (
        container.resolve(PerformanceTracker) if container.contains(PerformanceTracker) else None
    )
    virtual_stats_provider = (lambda: _virtual_stats(tracker)) if tracker is not None else None

    # Tercera fuente, y la más fina (Bloque 8): el resultado virtual de cada
    # señal, no el agregado por estrategia. Es lo que permite unir cada
    # operación con la resolución de su propia señal. Se adapta aquí por la
    # misma razón que la anterior.
    outcome_store = (
        container.resolve(VirtualOutcomeStore) if container.contains(VirtualOutcomeStore) else None
    )
    signal_outcomes_provider = (
        (lambda: _signal_outcomes(outcome_store)) if outcome_store is not None else None
    )

    # Cuarta fuente (Bloque 1): la salud del edge. No mide cuánto vale una
    # estrategia —de eso ya van las otras tres— sino si su edge SIGUE ahí. El
    # Meta Strategy Manager la usa sólo como freno del peso.
    edge_engine = (
        container.resolve(EdgeResearchEngine) if container.contains(EdgeResearchEngine) else None
    )
    edge_health_provider = (lambda: _edge_health(edge_engine)) if edge_engine is not None else None

    engine = MLEngine(
        settings,
        bus=bus,
        trades_provider=trades_provider,
        virtual_stats_provider=virtual_stats_provider,
        signal_outcomes_provider=signal_outcomes_provider,
        edge_health_provider=edge_health_provider,
    )
    container.register_instance(MLEngine, engine)

    # El notificador se suscribe al bus y traduce los hitos del ML a Discord.
    # El MLEngine no lo conoce: sólo publica eventos.
    notifications = container.resolve(NotificationService)
    container.register_instance(MLNotifier, MLNotifier(notifications, bus))

    # Cierre del lazo de gobierno: el MSM publicaba pesos y activaciones que
    # nadie aplicaba, así que el consenso seguía usando los pesos de arranque.
    # El aplicador traduce esos eventos a configuración del Strategy Engine,
    # auditando cada cambio. Sólo mueve configuración: no opera ni habilita live.
    if container.contains(StrategyEngine):
        container.register_instance(
            MetaGovernanceApplier,
            MetaGovernanceApplier(
                container.resolve(StrategyEngine),
                bus,
                audit=audit_log,
                enabled=settings.ml.meta.apply_governance,
            ),
        )

    # Explicación por operación (`/api/trades/{id}/explain`). Se construye aquí
    # porque cruza cuatro capas —journal, decisiones, evaluador continuo y
    # modelo activo— y ninguna debe conocer a las otras: recibe proveedores, no
    # objetos. Es de sólo lectura y el ML sigue sin decidir nada.
    if container.contains(TradeJournal) and container.contains(SignalHistoryStore):
        journal = container.resolve(TradeJournal)
        signal_history = container.resolve(SignalHistoryStore)
        explain_outcomes: Callable[[], dict[str, ExplainVerdict]] = (
            (lambda: _explain_verdicts(outcome_store)) if outcome_store is not None else dict
        )
        container.register_instance(
            TradeExplainer,
            TradeExplainer(
                journal.all,
                lambda: signal_history.decisions(limit=1000),
                explain_outcomes,
                # Sin modelo activo el predictor devuelve su propia degradación
                # elegante; se pasa igualmente para no duplicar esa lógica aquí.
                engine.explain_prediction,
            ),
        )


def _explain_verdicts(store: VirtualOutcomeStore) -> dict[str, ExplainVerdict]:
    """Adapt the per-signal virtual outcomes to the explainer's shape."""
    return {
        signal_id: ExplainVerdict(
            signal_id=signal_id,
            strategy=outcome.strategy,
            r_multiple=outcome.r_multiple,
            outcome=outcome.outcome,
        )
        for signal_id, outcome in store.index().items()
    }


def _signal_outcomes(store: VirtualOutcomeStore) -> dict[str, SignalOutcome]:
    """Adapt the per-signal virtual outcomes to the ML layer's shape.

    Se relee el fichero en cada llamada a propósito: el dataset se construye en
    el entrenamiento nocturno, no en el camino caliente, y un índice cacheado
    quedaría desactualizado justo respecto a las señales más recientes — que
    son las que interesan.
    """
    return {
        signal_id: SignalOutcome(
            signal_id=signal_id,
            strategy=outcome.strategy,
            r_multiple=outcome.r_multiple,
            outcome=outcome.outcome,
        )
        for signal_id, outcome in store.index().items()
    }


def _edge_health(engine: EdgeResearchEngine) -> dict[str, EdgeHealthStats]:
    """Adapt the Edge Research Engine's report to the ML layer's shape."""
    report = engine.last_report()
    if report is None:
        return {}
    return {
        entry.strategy: EdgeHealthStats(
            strategy=entry.strategy,
            status=entry.status,
            health_score=entry.health_score,
            factor=engine.factor(entry.strategy),
            sample=entry.sample,
        )
        for entry in report.strategies
    }


def _execution_pressure(engine: MicrostructureEngine) -> Callable[[str], float | None]:
    """Adapt the microstructure engine to the filter's reader signature."""

    def reader(symbol: str) -> float | None:
        snapshot = engine.snapshot(symbol)
        return snapshot.execution_pressure if snapshot.observable else None

    return reader


def _meta_risk_inputs(
    health: HealthMonitor | None,
    watchdog: Watchdog | None,
    quality: DataQualityEngine,
) -> MetaRiskInputs:
    """Adapt health/watchdog/data-quality into what the meta risk engine reads.

    Se lee el ULTIMO snapshot del health monitor, no se fuerza uno nuevo: pedir
    una medicion sincrona desde aqui meteria `psutil` en el camino de cada
    medicion de riesgo, y el monitor ya toma la suya en su propia cadencia.
    """
    snapshot = health.last_snapshot if health is not None else None
    statuses = (
        {name: status.value for name, status in watchdog.component_statuses.items()}
        if watchdog is not None
        else {}
    )
    return MetaRiskInputs(
        cpu_percent=None if snapshot is None else snapshot.cpu_percent,
        memory_percent=None if snapshot is None else snapshot.memory_percent,
        event_loop_lag_ms=None if snapshot is None else snapshot.event_loop_lag_ms,
        event_bus_queue=None if snapshot is None else snapshot.event_bus.get("queue_size"),
        component_statuses=statuses,
        data_quality_multiplier=quality.risk_multiplier(),
    )


def _optional_latency(value: object) -> float | None:
    """Read an optional latency metric without turning ``None`` into 0.0."""
    return float(value) if isinstance(value, int | float) else None


def _data_quality_inputs(
    metrics: FeedMetrics,
    books: OrderBookManager,
    state: MarketStateStore,
    symbols: list[str],
) -> DataQualityInputs:
    """Adapt the Data Engine's counters to what the quality engine measures.

    Se adapta aqui, en el composition root, para que el motor de calidad no
    dependa del Data Engine: asi se puede medir calidad sobre cualquier fuente
    de metricas, incluida una sintetica en tests.
    """
    book_status = books.status()
    synced = sum(1 for row in book_status.values() if row.get("synced"))
    with_data = sum(1 for symbol in symbols if state.ticker(symbol) is not None)
    return DataQualityInputs(
        messages=metrics.ws_messages.total,
        rejected=metrics.rejected,
        dropped=metrics.dropped,
        reconnections=metrics.reconnections,
        expected_symbols=len(symbols),
        symbols_with_data=with_data,
        books_synced=synced if book_status else None,
        books_total=len(book_status),
        # `ema_ms` es `None` hasta la primera muestra: se propaga tal cual para
        # que la senal quede como no observable en vez de como latencia cero.
        exchange_lag_ms=_optional_latency(metrics.data_latency.to_dict().get("ema_ms")),
    )


def _virtual_stats(tracker: PerformanceTracker) -> dict[str, VirtualStrategyStats]:
    """Adapt the continuous evaluator's stats to the ML layer's shape."""
    stats: dict[str, VirtualStrategyStats] = {}
    for name, perf in tracker.status()["strategies"].items():
        stats[name] = VirtualStrategyStats(
            strategy=name,
            evaluated=int(perf.get("evaluated") or 0),
            win_rate=float(perf.get("win_rate") or 0.0),
            profit_factor=float(perf.get("profit_factor") or 0.0),
            expectancy_r=float(perf.get("expectancy_r") or 0.0),
        )
    return stats


def _build_research(container: Container, settings: Settings, bus: EventBus) -> None:
    """Wire the Quant Research Lab (Fase 10): investiga, nunca opera.

    Regla absoluta: el laboratorio es independiente de producción. Descubre y
    valida estrategias sobre copias, y **nunca** habilita live trading; la
    promoción final siempre exige aprobación humana. Reutiliza el laboratorio de
    backtesting (Fase 6) si ya está cableado, y documenta en Notion si procede.

    Args:
        container: Contenedor DI en construcción.
        settings: Configuración central.
        bus: Event Bus ya registrado.
    """
    lab = container.resolve(BacktestLab) if container.contains(BacktestLab) else None
    documentation = (
        container.resolve(DocumentationService)
        if container.contains(DocumentationService)
        else None
    )
    research = ResearchLab(settings, bus=bus, lab=lab, documentation=documentation)
    container.register_instance(ResearchLab, research)

    # El notificador se suscribe al bus y traduce los hitos del laboratorio a
    # Discord. El ResearchLab no lo conoce: sólo publica eventos.
    notifications = container.resolve(NotificationService)
    container.register_instance(ResearchNotifier, ResearchNotifier(notifications, bus))

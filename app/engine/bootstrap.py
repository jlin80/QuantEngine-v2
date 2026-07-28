"""Composition root: aquí (y solo aquí) se construyen e inyectan los módulos.

Ningún módulo importa implementaciones de otro módulo: reciben sus
dependencias por constructor. Este archivo es el único que conoce el grafo.
"""

import logging
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
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.evaluation import PerformanceTracker
from app.engine.feature_store import FeatureStore
from app.engine.filters import build_filter_chain
from app.engine.market_context import MarketContextEngine
from app.engine.models import SignalRecord
from app.engine.plugins import PluginLoader
from app.engine.quant_core import QuantCore
from app.engine.regime_detection import RegimeDetector
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import HistoryWriter, SignalHistoryStore
from app.engine.strategy_engine import StrategyEngine
from app.engine.validators import SignalValidator
from app.execution.api import ExecutionCore
from app.execution.commission import CommissionEngine
from app.execution.execution_engine import ExecutionEngine
from app.execution.journal import TradeJournal
from app.execution.latency import LatencyEngine
from app.execution.notifications import ExecutionNotifier
from app.execution.order_manager import OrderManager
from app.execution.performance import PerformanceEngine
from app.execution.portfolio_manager import PortfolioManager
from app.execution.position_manager import PositionManager
from app.execution.risk_manager import RiskManager
from app.execution.sizing import PositionSizer
from app.execution.slippage import SlippageEngine
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
from app.ml.notifications import MLNotifier
from app.monitoring.health import HealthMonitor
from app.monitoring.watchdog import Watchdog
from app.notifications.channels.discord import DiscordWebhookChannel
from app.notifications.channels.discord_router import build_routed_discord
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService
from app.production.api import ProductionAPI
from app.production.audit import AuditLog
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
    timeframes = []
    for name in market.timeframes:
        try:
            timeframes.append(Timeframe(name))
        except ValueError:
            continue  # timeframe desconocido: se ignora (queda logueado en docs)
    aggregator = CandleAggregator(timeframes or [Timeframe.M1])
    books = OrderBookManager(max_depth=market.orderbook_depth * 4)
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
    )
    container.register_instance(TickCollector, collector)

    ws_manager = WebSocketManager()
    container.register_instance(WebSocketManager, ws_manager)

    registry = ProviderRegistry(market)
    # Si hay terminal MT5 compartido, el proveedor 'mt5' deja de ser un stub y usa
    # la conexión real (mismo terminal y lock que el broker de ejecución demo).
    if container.contains(MT5Connection):
        connection = container.resolve(MT5Connection)
        poll = settings.broker.mt5.tick_poll_seconds
        registry.register(
            "mt5", lambda _s: MT5MarketProvider(connection, poll_seconds=poll)
        )
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
    container.register_instance(FeatureStore, features)

    regime = RegimeDetector(market_service, quant.regime)
    context_engine = MarketContextEngine(market_service, features, regime, quant.context)
    container.register_instance(MarketContextEngine, context_engine)

    history_writer = HistoryWriter(container.resolve(DatabaseManager), quant.history)
    container.register_instance(HistoryWriter, history_writer)

    # Evaluación continua (Fase 4): resultado virtual de cada señal.
    tracker = PerformanceTracker(quant.evaluation, market_service)
    container.register_instance(PerformanceTracker, tracker)

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

    weights = {name: config.weight for name, config in quant.strategies.items()}
    consensus = ConsensusEngine(
        quant.consensus, weights, performance_factor=history.performance_factor
    )
    confidence = ConfidenceEngine(quant.confidence)
    filters = build_filter_chain(
        quant.filters,
        drawdown_reader=lambda: history.get_state("daily_drawdown_pct"),
        recent_decisions=lambda: history.decisions(limit=50),
    )

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
    )
    container.register_instance(DecisionEngine, decision_engine)

    loader = PluginLoader(list(quant.plugin_dirs))
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
    )
    container.register_instance(StrategyEngine, strategy_engine)

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
    performance = PerformanceEngine(initial_balance)

    context_engine = (
        container.resolve(MarketContextEngine) if container.contains(MarketContextEngine) else None
    )

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

    engine = MLEngine(settings, bus=bus, trades_provider=trades_provider)
    container.register_instance(MLEngine, engine)

    # El notificador se suscribe al bus y traduce los hitos del ML a Discord.
    # El MLEngine no lo conoce: sólo publica eventos.
    notifications = container.resolve(NotificationService)
    container.register_instance(MLNotifier, MLNotifier(notifications, bus))


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

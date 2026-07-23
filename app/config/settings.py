"""Definición tipada de toda la configuración del sistema.

Convención de variables: ``QE_<SECCION>__<CAMPO>`` (doble guion bajo para
anidamiento). Precedencia: entorno del proceso > ``.env`` > ``config/<env>.env``.
Ningún secreto se hardcodea; los secretos usan ``SecretStr``.
"""

import functools
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.environment import Environment, detect_environment

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TradingSettings(BaseModel):
    """Parámetros globales de trading (contratos para fases futuras)."""

    instruments: list[str] = Field(
        default=["BTCUSD", "ETHUSD", "XAUUSD"],
        description="Instrumentos objetivo del motor (cripto + oro).",
    )
    base_currency: str = "USD"


class MT5BrokerSettings(BaseModel):
    """Conexión y parámetros del terminal MetaTrader 5 (cuenta demo).

    Los secretos usan ``SecretStr`` y jamás se registran. ``login`` en 0 y
    ``server`` vacío significan "sin configurar": el broker MT5 no arranca.
    """

    login: int = 0
    password: SecretStr = SecretStr("")
    server: str = ""
    terminal_path: str = ""
    timeout_ms: int = 60_000
    portable: bool = False
    deviation_points: int = 20  # desviación de precio tolerada en órdenes a mercado
    magic: int = 777_001  # etiqueta las órdenes de este motor en el terminal
    tick_poll_seconds: float = 0.5  # cadencia de polling de ticks del feed MT5


class BrokerSettings(BaseModel):
    """Credenciales/parámetros del broker de ejecución.

    ``name`` selecciona el adaptador de ejecución cuando el modo es ``demo``
    (hoy sólo ``mt5_exness``). Vacío = sin broker real (paper puro).
    """

    name: str = ""
    api_key: SecretStr = SecretStr("")
    api_secret: SecretStr = SecretStr("")
    mt5: MT5BrokerSettings = Field(default_factory=MT5BrokerSettings)


class DatabaseSettings(BaseModel):
    """PostgreSQL vía SQLAlchemy async."""

    host: str = "localhost"
    port: int = 5432
    user: str = "quant"
    password: SecretStr = SecretStr("")
    name: str = "quant_engine"
    pool_size: int = 5
    max_overflow: int = 5
    pool_pre_ping: bool = True
    echo: bool = False

    @property
    def dsn(self) -> str:
        """Async DSN (asyncpg) built from parts."""
        password = self.password.get_secret_value()
        auth = f"{self.user}:{password}" if password else self.user
        return f"postgresql+asyncpg://{auth}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_dsn(self) -> str:
        """Sync DSN (psycopg/alembic offline) built from parts."""
        password = self.password.get_secret_value()
        auth = f"{self.user}:{password}" if password else self.user
        return f"postgresql://{auth}@{self.host}:{self.port}/{self.name}"


class CacheSettings(BaseModel):
    """Redis como cache primario, con degradación a memoria."""

    host: str = "localhost"
    port: int = 6379
    password: SecretStr = SecretStr("")
    db: int = 0
    socket_timeout_seconds: float = 3.0
    retry_cooldown_seconds: float = 30.0

    @property
    def url(self) -> str:
        """Redis connection URL."""
        password = self.password.get_secret_value()
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class DiscordSettings(BaseModel):
    """Discord Webhook — único medio de notificación del sistema.

    ``webhook_url`` es el canal por defecto. ``channels`` mapea canales lógicos
    (``sistema``/``trading``/``errores``/``backtesting``/``ml``/``produccion``/
    ``reportes``) a webhooks propios; lo no mapeado cae al webhook por defecto,
    así multicanal es opcional y retrocompatible.
    """

    enabled: bool = True
    webhook_url: SecretStr = SecretStr("")
    min_level: str = "info"
    timeout_seconds: float = 10.0
    max_retries: int = 3
    # Webhooks por canal lógico: {"errores": "https://discord.com/api/webhooks/..."}.
    channels: dict[str, SecretStr] = Field(default_factory=dict)
    # Ruteo fuente→canal lógico: {"kill_switch": "produccion", "ml": "ml"}.
    routing: dict[str, str] = Field(default_factory=dict)


class NotionSettings(BaseModel):
    """Notion (DocumentationService) — documentación automática de la Fase 9.

    Cuando ``enabled`` es ``True`` la bitácora escribe también en Notion vía la
    API oficial. Si Notion no responde, las entradas se encolan en disco
    (``queue_path``) y un job del scheduler reintenta — nunca se pierde una
    entrada ni se bloquea la operación (la spec exige "cola de sincronización").
    """

    enabled: bool = False
    api_key: SecretStr = SecretStr("")
    database_id: str = ""
    version: str = "2022-06-28"  # Notion-Version header
    timeout_seconds: float = 15.0
    max_retries: int = 3
    queue_path: Path = _PROJECT_ROOT / "data" / "production" / "notion_queue.jsonl"


class DashboardSettings(BaseModel):
    """API FastAPI + frontend futuro."""

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    cors_origins: list[str] = Field(default=["http://localhost:3000"])


class MLTrainingSettings(BaseModel):
    """Entrenamiento de modelos (Fase 7).

    El ML se entrena con el historial generado por el propio motor (operaciones
    cerradas), nunca prediciendo el precio directamente. Etiqueta binaria:
    calidad de la operación (ganadora/perdedora).
    """

    min_samples: int = 60  # mínimo de operaciones antes de entrenar
    test_size: float = 0.25  # fracción reservada a validación temporal (holdout)
    cv_folds: int = 5  # pliegues de validación cruzada
    walk_forward_folds: int = 4  # pliegues walk-forward (out-of-sample temporal)
    random_seed: int = 7
    nightly_hour_utc: int = 3  # hora del entrenamiento nocturno programado
    retrain_min_new_trades: int = 30  # operaciones nuevas para reentrenar


class MLModelSettings(BaseModel):
    """Hiperparámetros por defecto de los modelos (todos intercambiables)."""

    default_model: str = "logistic_regression"
    # Regresión logística (modelo explicable por defecto).
    learning_rate: float = 0.1
    epochs: int = 400
    l2: float = 0.001
    # Árboles / bosques.
    n_estimators: int = 60
    max_depth: int = 6
    min_samples_split: int = 6
    max_features: float = 0.7  # fracción de features candidatas por corte


class MLValidationSettings(BaseModel):
    """Puerta de validación antes de activar un modelo (seguridad).

    Ningún modelo se activa automáticamente sin superar estos mínimos y, si se
    exige, sin batir al modelo anterior. Nunca se activa un modelo inferior.
    """

    min_auc: float = 0.55
    min_accuracy: float = 0.55
    min_samples: int = 40
    require_beat_previous: bool = True
    min_improvement: float = 0.0  # mejora mínima de AUC sobre el modelo activo


class MLDriftSettings(BaseModel):
    """Detección de deriva (feature / concept / performance / model)."""

    psi_warning: float = 0.10  # Population Stability Index: aviso
    psi_alert: float = 0.25  # PSI: alerta (deriva significativa)
    performance_drop: float = 0.10  # caída de AUC/accuracy vs línea base
    min_samples: int = 30
    reduce_confidence_factor: float = 0.5  # factor al detectar deriva


class MLAdvisorSettings(BaseModel):
    """IA de asesoramiento (nunca opera; solo aconseja al Decision Engine)."""

    min_similar_trades: int = 20  # historial mínimo para una recomendación
    advise_against_below: float = 0.40  # P(éxito) por debajo → desaconsejar
    low_confidence_below: float = 0.55  # P(éxito) por debajo → confianza baja


class MLMetaStrategySettings(BaseModel):
    """Meta Strategy Manager: gestiona activación, prioridad y peso (Fase 7).

    Por encima del Strategy Engine y del ML. Nunca modifica el código de las
    estrategias: solo su activación y ponderación mediante configuración.
    """

    enabled: bool = True
    evaluation_interval_seconds: float = 3600.0
    min_trades: int = 20  # operaciones mínimas para evaluar una estrategia
    lookback_trades: int = 60  # ventana de rendimiento reciente
    disable_expectancy_r: float = -0.05  # expectativa (R) que degrada
    disable_profit_factor: float = 0.90  # PF por debajo → degradada
    disable_after_periods: int = 3  # evaluaciones fallidas seguidas → desactivar
    min_weight: float = 0.10
    max_weight: float = 2.00
    weight_smoothing: float = 0.50  # suavizado del ajuste de peso [0-1]


class MLSettings(BaseModel):
    """Machine Learning, IA y aprendizaje continuo (Fase 7).

    Regla absoluta: el ML **asesora, no decide**. Ninguna recomendación abre o
    cierra posiciones ni habilita live trading; toda salida pasa por el Decision
    Engine y el Risk Manager. El sistema sigue operando solo en paper trading.
    """

    enabled: bool = False
    models_dir: Path = _PROJECT_ROOT / "data" / "models"  # compat. histórica
    registry_dir: Path = _PROJECT_ROOT / "data" / "ml" / "registry"
    experiments_dir: Path = _PROJECT_ROOT / "data" / "ml" / "experiments"
    datasets_dir: Path = _PROJECT_ROOT / "data" / "ml" / "datasets"
    # Nunca activar un modelo automáticamente sin superar la validación; incluso
    # superándola, sólo se autoactiva si esta bandera está encendida.
    auto_activate: bool = False
    # Modelos que prueba el AutoML (los backends pesados quedan preparados).
    automl_models: list[str] = Field(
        default=["logistic_regression", "decision_tree", "random_forest", "extra_trees"]
    )
    training: MLTrainingSettings = Field(default_factory=MLTrainingSettings)
    model: MLModelSettings = Field(default_factory=MLModelSettings)
    validation: MLValidationSettings = Field(default_factory=MLValidationSettings)
    drift: MLDriftSettings = Field(default_factory=MLDriftSettings)
    advisor: MLAdvisorSettings = Field(default_factory=MLAdvisorSettings)
    meta: MLMetaStrategySettings = Field(default_factory=MLMetaStrategySettings)


class RiskSettings(BaseModel):
    """Límites globales de riesgo (contratos para fases futuras)."""

    max_daily_drawdown_pct: float = 3.0
    max_open_positions: int = 5
    risk_per_trade_pct: float = 0.5


class LoggingSettings(BaseModel):
    """Sistema de logging estructurado con rotación."""

    level: str = "INFO"
    json_format: bool = False
    directory: Path = _PROJECT_ROOT / "logs"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    module_files: list[str] = Field(
        default=[
            "app.event_bus",
            "app.notifications",
            "app.health_monitor",
            "app.watchdog",
            "app.scheduler",
            "app.cache",
            "app.api",
        ],
        description="Loggers que además escriben a su propio archivo rotado.",
    )


class PaperSettings(BaseModel):
    """Paper trading — preparado, sin implementación en Fase 1."""

    initial_balance: float = 10_000.0
    slippage_bps: float = 1.0


class QualificationCriteriaSettings(BaseModel):
    """Umbrales mínimos para promover una estrategia (Fase 6).

    Si una estrategia no supera estos mínimos, el Strategy Qualification
    Pipeline la rechaza automáticamente. Ninguna estrategia llega a paper (y
    mucho menos a live) sin pasar por aquí.
    """

    min_trades: int = 30
    min_profit_factor: float = 1.60
    min_sharpe: float = 1.20
    max_drawdown_pct: float = 12.0
    min_expectancy: float = 0.0  # expectativa neta > 0
    min_sqn: float = 2.0
    min_win_rate: float = 0.0
    require_walk_forward: bool = True
    require_monte_carlo: bool = True
    require_beat_benchmark: bool = True
    # Monte Carlo: percentil inferior del drawdown que aún debe cumplir el tope.
    monte_carlo_max_drawdown_pct: float = 20.0
    # Robustez: fracción mínima de regímenes/escenarios que deben ser rentables.
    min_robust_scenarios_pct: float = 0.60


class OptimizerSettings(BaseModel):
    """Optimización automática de parámetros (Fase 6)."""

    method: str = "grid"  # grid | random | bayesian | optuna | genetic
    max_evaluations: int = 200  # tope para random/bayesian/genetic
    objective: str = "sharpe"  # métrica a maximizar
    random_seed: int = 7
    # Algoritmo genético (estructura preparada).
    population_size: int = 20
    generations: int = 10
    mutation_rate: float = 0.15


class WalkForwardSettings(BaseModel):
    """Walk Forward Analysis (Fase 6)."""

    scheme: str = "rolling"  # rolling | expanding | anchored
    train_size: int = 500  # velas de entrenamiento
    validation_size: int = 125  # velas de validación (out-of-sample)
    step: int = 125  # avance de la ventana


class MonteCarloSettings(BaseModel):
    """Simulación Monte Carlo (Fase 6)."""

    simulations: int = 1000
    method: str = "resample"  # resample | shuffle
    initial_capital: float = 10_000.0
    ruin_threshold_pct: float = 50.0  # pérdida de capital considerada ruina
    confidence: float = 0.95
    random_seed: int = 7


class BacktestingSettings(BaseModel):
    """Laboratorio cuantitativo: backtesting, optimización y validación (Fase 6).

    Regla de oro heredada: el laboratorio valida estrategias con evidencia
    estadística; nada de esto habilita live trading.
    """

    enabled: bool = False
    data_dir: Path = _PROJECT_ROOT / "data" / "history"
    results_dir: Path = _PROJECT_ROOT / "data" / "backtesting" / "results"
    experiments_dir: Path = _PROJECT_ROOT / "data" / "backtesting" / "experiments"
    versions_dir: Path = _PROJECT_ROOT / "data" / "backtesting" / "versions"
    initial_balance: float = 10_000.0
    default_spread_bps: float = 2.0  # spread sintético si el dataset no lo trae
    default_timeframe: str = "1m"
    risk_free_rate: float = 0.0  # para Sharpe/Sortino anualizados
    criteria: QualificationCriteriaSettings = Field(default_factory=QualificationCriteriaSettings)
    optimizer: OptimizerSettings = Field(default_factory=OptimizerSettings)
    walk_forward: WalkForwardSettings = Field(default_factory=WalkForwardSettings)
    monte_carlo: MonteCarloSettings = Field(default_factory=MonteCarloSettings)


class MarketProviderSettings(BaseModel):
    """Configuración de un proveedor de datos concreto."""

    enabled: bool = False
    ws_url: str = ""
    rest_url: str = ""
    api_key: SecretStr = SecretStr("")
    api_secret: SecretStr = SecretStr("")
    api_passphrase: SecretStr = SecretStr("")
    testnet: bool = False


class MarketWSSettings(BaseModel):
    """Parámetros de las conexiones WebSocket del Data Engine."""

    ping_interval_seconds: float = 20.0
    stale_after_seconds: float = 30.0
    backoff_base_seconds: float = 1.0
    backoff_factor: float = 2.0
    backoff_cap_seconds: float = 60.0
    max_retries: int = 0
    compression: bool = True


class MarketQualitySettings(BaseModel):
    """Umbrales del validador de calidad de datos."""

    max_price_jump_pct: float = 20.0
    max_volume: float = 1e12
    future_tolerance_seconds: float = 5.0
    stale_data_seconds: float = 120.0
    duplicate_window: int = 4096
    out_of_order_grace_seconds: float = 2.0


class MarketStorageSettings(BaseModel):
    """Persistencia de ticks/velas (batching + spill si la DB cae)."""

    enabled: bool = True
    store_ticks: bool = True
    store_candles: bool = True
    flush_interval_seconds: float = 5.0
    batch_size: int = 1000
    buffer_limit: int = 100_000
    spill_dir: Path = _PROJECT_ROOT / "data" / "spill"


class MarketSettings(BaseModel):
    """Data Engine: proveedores, suscripciones y pipeline de datos."""

    enabled: bool = False
    default_provider: str = "binance"
    symbols: list[str] = Field(
        default=["BTCUSDT", "ETHUSDT"],
        description="Símbolos a suscribir al arrancar.",
    )
    symbol_provider: dict[str, str] = Field(
        default_factory=dict,
        description="Ruteo símbolo→proveedor; lo no listado usa default_provider.",
    )
    channels: list[str] = Field(
        default=["ticker", "trades"],
        description="Canales por defecto al suscribir un símbolo.",
    )
    timeframes: list[str] = Field(
        default=["1m", "5m"],
        description="Timeframes que construye el agregador local.",
    )
    aggregate_from_ticks: bool = Field(
        default=False,
        description=(
            "Construir velas desde quotes/ticker cuando el proveedor no emite "
            "un tape de trades (p. ej. MT5). Déjalo en False para feeds con "
            "trades reales (Binance) o se duplicarían las velas."
        ),
    )
    backfill_on_subscribe: int = Field(
        default=200,
        description=(
            "Velas históricas a sembrar por timeframe al suscribir (warmup). "
            "0 lo desactiva. No dispara evaluación (sólo llena el store)."
        ),
    )
    orderbook_depth: int = 50
    recent_trades_limit: int = 500
    candle_history_limit: int = 500
    collector_queue_size: int = 100_000
    providers: dict[str, MarketProviderSettings] = Field(default_factory=dict)
    ws: MarketWSSettings = Field(default_factory=MarketWSSettings)
    quality: MarketQualitySettings = Field(default_factory=MarketQualitySettings)
    storage: MarketStorageSettings = Field(default_factory=MarketStorageSettings)

    def provider_settings(self, name: str) -> MarketProviderSettings:
        """Return the settings for a provider (defaults si no está declarado).

        Args:
            name: Nombre del proveedor.

        Returns:
            Su configuración, o una instancia por defecto.
        """
        return self.providers.get(name, MarketProviderSettings())

    def provider_for(self, symbol: str) -> str:
        """Resolve which provider serves a symbol.

        Args:
            symbol: Símbolo interno.

        Returns:
            El proveedor ruteado o ``default_provider``.
        """
        return self.symbol_provider.get(symbol.upper(), self.default_provider)


class QuantStrategySettings(BaseModel):
    """Configuración por estrategia (plugins)."""

    enabled: bool = True
    weight: float = 1.0
    parameters: dict[str, object] = Field(default_factory=dict)


class QuantConsensusSettings(BaseModel):
    """Motor de consenso y umbrales de decisión."""

    method: str = "weighted_average"
    min_signals: int = 1
    min_score: float = 60.0
    min_confidence: float = 0.5
    min_agreement: float = 0.5
    default_weight: float = 1.0
    # Multiplicadores por régimen: {"trending": {"momentum": 1.5}, ...}
    regime_multipliers: dict[str, dict[str, float]] = Field(default_factory=dict)


class QuantConfidenceSettings(BaseModel):
    """Pesos de los factores del Confidence Engine (se normalizan)."""

    signal_confidence: float = 0.25
    agreement: float = 0.20
    data_quality: float = 0.20
    liquidity: float = 0.10
    volatility: float = 0.10
    confirmation: float = 0.05
    history: float = 0.10


class QuantContextSettings(BaseModel):
    """Market Context Engine: sesiones, umbrales y noticias."""

    context_timeframe: str = "1m"
    # Sesiones por hora UTC [inicio, fin).
    session_hours: dict[str, tuple[int, int]] = Field(
        default_factory=lambda: {"asia": (0, 9), "europe": (7, 16), "america": (13, 22)}
    )
    max_spread_bps: float = 5.0
    min_volume: float = 0.0
    atr_pct_low: float = 0.05
    atr_pct_high: float = 0.8
    stale_data_seconds: float = 30.0
    # Ventanas de noticias: [["2026-07-17T14:25Z","2026-07-17T14:35Z"], ...]
    news_blackouts: list[tuple[str, str]] = Field(default_factory=list)


class QuantRegimeSettings(BaseModel):
    """Detector de régimen."""

    timeframe: str = "1m"
    lookback: int = 50
    trending_efficiency: float = 0.35
    expansion_ratio: float = 1.4
    compression_ratio: float = 0.7
    breakout_lookback: int = 20


class QuantFiltersSettings(BaseModel):
    """Filtros previos a la decisión."""

    enabled: list[str] = Field(
        default=[
            "session",
            "spread",
            "volatility",
            "liquidity",
            "news",
            "drawdown",
            "correlation",
        ]
    )
    allowed_sessions: list[str] = Field(default=["asia", "europe", "america"])
    # Símbolos que cotizan 24/7 (cripto): el filtro de sesión no aplica —
    # forex/XAUUSD sí cierra fuera de las ventanas configuradas, cripto no.
    always_open_symbols: list[str] = Field(default_factory=list)
    max_drawdown_pct: float = 5.0
    correlation_groups: list[list[str]] = Field(default_factory=list)
    correlation_window_minutes: float = 30.0


class QuantHistorySettings(BaseModel):
    """Historial de señales y decisiones (nunca se borra información)."""

    memory_limit: int = 10_000
    persist: bool = True
    flush_interval_seconds: float = 10.0
    batch_size: int = 500
    spill_dir: Path = _PROJECT_ROOT / "data" / "spill"


class QuantEvaluationSettings(BaseModel):
    """Evaluación continua: resultado virtual de cada señal por estrategia.

    Las métricas NO se usan para operar todavía; se almacenan para que fases
    posteriores ajusten pesos dinámicamente sin tocar el código.
    """

    enabled: bool = True
    timeframe: str = "1m"
    check_interval_seconds: float = 30.0
    max_holding_minutes: float = 240.0
    false_signal_bars: int = 3
    r_history_limit: int = 500
    snapshot_path: Path = _PROJECT_ROOT / "data" / "performance" / "strategy_stats.json"
    snapshot_interval_seconds: float = 300.0


class QuantSettings(BaseModel):
    """Quant Core (Fase 3): estrategias, consenso, contexto y decisión."""

    enabled: bool = False
    # Directorios de plugins: las categorías de la biblioteca de estrategias.
    plugin_dirs: list[Path] = Field(
        default_factory=lambda: [
            _PROJECT_ROOT / "app" / "strategies" / category
            for category in (
                "trend",
                "momentum",
                "orderflow",
                "smc",
                "volume",
                "volatility",
                "mean_reversion",
                "breakout",
            )
        ]
    )
    auto_discover: bool = True
    signal_ttl_seconds: float = 300.0
    dedupe_window_seconds: float = 60.0
    strategies: dict[str, QuantStrategySettings] = Field(default_factory=dict)
    consensus: QuantConsensusSettings = Field(default_factory=QuantConsensusSettings)
    confidence: QuantConfidenceSettings = Field(default_factory=QuantConfidenceSettings)
    context: QuantContextSettings = Field(default_factory=QuantContextSettings)
    regime: QuantRegimeSettings = Field(default_factory=QuantRegimeSettings)
    filters: QuantFiltersSettings = Field(default_factory=QuantFiltersSettings)
    history: QuantHistorySettings = Field(default_factory=QuantHistorySettings)
    evaluation: QuantEvaluationSettings = Field(default_factory=QuantEvaluationSettings)

    def strategy_settings(self, name: str) -> QuantStrategySettings:
        """Config de una estrategia (defaults si no está declarada)."""
        return self.strategies.get(name, QuantStrategySettings())


class HealthSettings(BaseModel):
    """Health Monitor."""

    check_interval_seconds: float = 30.0
    cpu_warn_pct: float = 85.0
    memory_warn_pct: float = 85.0
    disk_warn_pct: float = 90.0


class WatchdogSettings(BaseModel):
    """Watchdog de módulos."""

    check_interval_seconds: float = 10.0
    default_heartbeat_timeout_seconds: float = 60.0
    max_restarts: int = 3
    error_threshold: int = 5
    error_window_seconds: float = 60.0


class CommissionSettings(BaseModel):
    """Motor de comisiones (nunca se hardcodea; cada broker su estructura)."""

    model: str = "per_notional"  # per_notional | per_unit | fixed | tiered
    maker_bps: float = 1.0
    taker_bps: float = 2.0
    per_unit: float = 0.0
    fixed: float = 0.0
    minimum: float = 0.0
    # Overrides por símbolo: {"XAUUSD": {"taker_bps": 3.0}}
    per_symbol: dict[str, dict[str, float]] = Field(default_factory=dict)


class SlippageSettings(BaseModel):
    """Modelo de slippage: volumen, liquidez, volatilidad, horario y orden."""

    model: str = "dynamic"  # none | fixed | dynamic
    base_bps: float = 1.0
    volatility_coeff: float = 0.5  # bps adicionales por 1% de ATR
    liquidity_coeff: float = 0.5  # bps adicionales cuando falta profundidad
    size_coeff: float = 0.3  # bps adicionales por impacto de tamaño
    # Multiplicadores por sesión UTC (fuera de sesión el slippage sube).
    session_multipliers: dict[str, float] = Field(
        default_factory=lambda: {"asia": 1.2, "europe": 1.0, "america": 1.0, "off": 1.5}
    )
    stop_order_extra_bps: float = 1.0  # penalización de órdenes stop (peor fill)
    max_bps: float = 50.0


class LatencySettings(BaseModel):
    """Latencia simulada (red + broker + exchange + interna) que mueve el fill."""

    enabled: bool = True
    network_ms: float = 20.0
    broker_ms: float = 15.0
    exchange_ms: float = 10.0
    internal_ms: float = 5.0
    jitter_ms: float = 10.0  # amplitud aleatoria uniforme añadida
    # bps de deriva de precio por cada 100 ms de latencia total.
    price_drift_bps_per_100ms: float = 0.5


class SizingSettings(BaseModel):
    """Métodos de position sizing (todos configurables)."""

    method: str = "fixed_risk"
    # fixed_amount | percent | atr | fixed_risk | dynamic_risk | kelly
    fixed_amount: float = 100.0  # notional fijo por operación
    percent_of_equity: float = 2.0  # % del equity como notional
    risk_per_trade_pct: float = 0.5  # % del equity arriesgado hasta el stop
    atr_stop_multiplier: float = 1.5  # distancia de stop = ATR × múltiplo
    atr_period: int = 14
    # Piso de la distancia del stop como % del precio: evita stops más
    # angostos que el propio spread cuando el ATR (velas desde ticks
    # dispersos) sale artificialmente bajo — sin esto el stop se dispara
    # casi al entrar por simple ruido/spread, no por movimiento real.
    min_stop_pct: float = 0.15
    reward_risk: float = 1.5  # objetivo = riesgo × esta relación
    kelly_fraction: float = 0.25  # fracción parcial de Kelly
    max_position_pct: float = 20.0  # tope de notional como % del equity
    min_quantity: float = 0.0


class ExecutionRiskSettings(BaseModel):
    """Risk Manager profesional: límites, filtros y cortacircuitos."""

    max_risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 8.0
    max_monthly_loss_pct: float = 15.0
    max_consecutive_losses: int = 5
    max_open_positions: int = 5
    max_positions_per_symbol: int = 1
    max_exposure_pct: float = 100.0  # exposición total / equity
    max_symbol_exposure_pct: float = 40.0
    max_correlation_exposure_pct: float = 60.0
    correlation_groups: list[list[str]] = Field(default_factory=list)
    min_liquidity: float = 0.0  # volumen reciente mínimo
    max_spread_bps: float = 10.0
    # Circuit breaker: pérdida máxima dentro de una ventana móvil.
    circuit_breaker_loss_pct: float = 5.0
    circuit_breaker_window_minutes: float = 15.0
    # Kill switch: drawdown máximo tolerado sobre el equity pico.
    kill_switch_drawdown_pct: float = 20.0


class ExecutionSettings(BaseModel):
    """Motor de ejecución y paper trading (Fase 5).

    Regla de oro: ``mode`` solo admite ``paper``. El live trading está
    prohibido en esta fase y un valor distinto se fuerza a ``paper``.
    """

    enabled: bool = False
    mode: str = "paper"  # SOLO paper en Fase 5 (live prohibido)
    initial_balance: float = 10_000.0  # semilla de balance (paper, o fallback en demo)
    use_broker_balance: bool = True  # en modo demo, sembrar con el balance REAL de la cuenta
    base_currency: str = "USD"
    leverage: float = 1.0
    manage_interval_seconds: float = 2.0  # cadencia del bucle de gestión
    reject_probability: float = 0.0  # prob. de rechazo simulado [0-1]
    partial_fill_probability: float = 0.0  # prob. de ejecución parcial [0-1]
    gap_threshold_bps: float = 30.0  # salto de precio considerado gap
    default_time_in_force: str = "gtc"
    break_even_r: float = 1.0  # mover stop a BE tras +1R (0 desactiva)
    trailing_enabled: bool = True
    trailing_atr_multiple: float = 2.0
    max_holding_minutes: float = 240.0  # salida por tiempo (0 desactiva)
    exit_on_regime_change: bool = True
    # Tiempo mínimo antes de que un cambio de régimen pueda cerrar: sin esto,
    # el régimen "parpadea" entre etiquetas vela a vela (más en cripto, velas
    # 1m ruidosas) y corta la posición casi al entrar, antes de que se mueva.
    regime_change_min_holding_seconds: float = 180.0
    report_interval_seconds: float = 3600.0  # resumen periódico a Discord
    journal_path: Path = _PROJECT_ROOT / "data" / "execution" / "journal.jsonl"
    persist_journal: bool = True
    commission: CommissionSettings = Field(default_factory=CommissionSettings)
    slippage: SlippageSettings = Field(default_factory=SlippageSettings)
    latency: LatencySettings = Field(default_factory=LatencySettings)
    sizing: SizingSettings = Field(default_factory=SizingSettings)
    risk: ExecutionRiskSettings = Field(default_factory=ExecutionRiskSettings)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        """Reject unknown modes instead of ignoring them silently.

        Hasta la Fase 8 ``mode`` era un ``str`` libre: ``QE_EXECUTION__MODE=lvie``
        se aceptaba sin error y no hacía nada. Ahora sólo ``paper`` y ``live``
        son válidos — pedir ``live`` sigue sin habilitarlo (hace falta la cadena
        completa del Live Gate), pero al menos es una intención declarada.
        """
        normalized = value.strip().lower()
        if normalized not in ("paper", "demo", "live"):
            raise ValueError(
                f"execution.mode debe ser 'paper', 'demo' o 'live', no {value!r}"
            )
        return normalized

    @property
    def is_live(self) -> bool:
        """Whether live trading is *requested* (requesting is not enabling)."""
        return self.mode == "live"

    @property
    def is_demo(self) -> bool:
        """Whether demo trading (real orders to a demo account) is requested."""
        return self.mode == "demo"

    def resolved_mode(self) -> str:
        """Static safety floor.

        ``demo`` se selecciona directamente por configuración: enruta órdenes
        reales a una cuenta **demo**, sin dinero real, así que no exige el Live
        Gate. ``live`` (dinero real), en cambio, NUNCA se resuelve aquí: la
        configuración por sí sola no puede seleccionarlo porque no conoce el
        estado en vivo del sistema (salud, drift, calificación, aprobación del
        operador). Esa resolución autorizada vive en
        :class:`app.production.live.mode.ModeResolver`, que consulta este suelo y
        además el Live Gate completo. Cualquier valor distinto de ``demo`` cae a
        ``paper``.
        """
        return "demo" if self.mode == "demo" else "paper"


class LiveGateSettings(BaseModel):
    """Criterios obligatorios para habilitar Live Trading (Fase 9).

    Todos son configurables y **ninguno es saltable**: el Live Gate exige que se
    cumplan simultáneamente. Subir un umbral endurece el gate; bajarlo invalida
    cualquier aprobación previa del operador (el hash del reporte cambia).
    """

    # Umbrales estadísticos sobre el historial de paper trading.
    min_profit_factor: float = 1.60
    min_sharpe: float = 1.20
    max_drawdown_pct: float = 12.0
    min_paper_trades: int = 200
    min_paper_days: float = 30.0
    # Validación científica (Fase 6).
    require_qualification: bool = True
    require_walk_forward: bool = True
    require_monte_carlo: bool = True
    # Machine Learning (Fase 7).
    require_ml_stable: bool = True
    require_no_critical_drift: bool = True
    # Calidad e incidencias.
    min_test_coverage_pct: float = 80.0
    max_open_critical_errors: int = 0
    # Infraestructura viva.
    require_healthy_system: bool = True
    require_broker_connected: bool = True
    require_discord: bool = True
    require_database: bool = True
    require_redis: bool = False  # degradable: Redis cae a memoria por diseño
    require_watchdog: bool = True
    require_scheduler: bool = True
    require_risk_manager: bool = True
    # Aprobación humana explícita desde el dashboard.
    require_operator_approval: bool = True
    approval_ttl_hours: float = 24.0


class SafeModeSettings(BaseModel):
    """Safe Mode: degradación controlada ante cualquier duda del sistema."""

    enabled: bool = True
    # Por defecto se mantienen las posiciones abiertas y sólo se cierran las
    # entradas nuevas: cerrar a ciegas en un momento malo hace más daño.
    close_positions_on_enter: bool = False
    recovery_cycles: int = 3  # ciclos sanos consecutivos antes de salir
    max_latency_ms: float = 2000.0
    max_cpu_pct: float = 95.0
    max_memory_pct: float = 95.0
    max_drawdown_pct: float = 15.0
    error_threshold: int = 10
    error_window_seconds: float = 300.0
    broker_failure_threshold: int = 3


class KillSwitchSettings(BaseModel):
    """Kill Switch global: manual, automático, programado o por riesgo."""

    enabled: bool = True
    persist_state: bool = True
    # Un kill switch que se olvida al reiniciar no es un kill switch.
    state_path: Path = _PROJECT_ROOT / "data" / "production" / "kill_switch.json"
    require_reason_to_release: bool = True
    scheduled_utc: str = ""  # "HH:MM" para un corte programado diario


class RecoverySettings(BaseModel):
    """Recuperación de estado al reiniciar — nunca empezar de cero."""

    enabled: bool = True
    snapshot_path: Path = _PROJECT_ROOT / "data" / "production" / "state.json"
    snapshot_interval_seconds: float = 30.0
    reload_journal: bool = True
    max_age_hours: float = 72.0  # snapshots más viejos se ignoran


class ReportingSettings(BaseModel):
    """Reportes operativos automáticos (Fase 9).

    Resumen operativo cada hora y reporte completo diario (PnL, capital,
    drawdown, win rate, PF, trades, errores, CPU/RAM/latencia, estado de ML,
    brokers, Notion y Discord). Se entregan al canal lógico ``reportes``.
    """

    enabled: bool = True
    hourly: bool = True
    daily: bool = True
    hourly_interval_seconds: float = 3600.0
    daily_interval_seconds: float = 86_400.0
    channel: str = "reportes"  # canal lógico de Discord


class BackupSettings(BaseModel):
    """Backups automáticos con rotación, compresión y verificación (Fase 9)."""

    enabled: bool = True
    backup_dir: Path = _PROJECT_ROOT / "data" / "backups"
    # Rutas que entran en cada respaldo (estado crítico, no dependencias).
    sources: list[Path] = Field(
        default_factory=lambda: [
            _PROJECT_ROOT / "data" / "production",
            _PROJECT_ROOT / "data" / "execution",
            _PROJECT_ROOT / "data" / "ml" / "registry",
            _PROJECT_ROOT / "logs" / "audit.jsonl",
        ]
    )
    retention: int = 14  # respaldos a conservar (rotación)
    interval_seconds: float = 86_400.0  # respaldo automático diario
    compress: bool = True
    verify_on_create: bool = True


class SecuritySettings(BaseModel):
    """Endurecimiento de seguridad (Fase 9): rate limiting, secretos, API."""

    enabled: bool = True
    # Rate limiting de la API (token bucket por cliente).
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120  # peticiones permitidas por ventana
    rate_limit_window_seconds: float = 60.0
    security_headers: bool = True  # cabeceras defensivas en cada respuesta
    # Rotación de secretos: sólo vigila y avisa, nunca imprime el secreto.
    secret_max_age_days: float = 90.0
    require_secrets_in_production: bool = True


class UpdateSettings(BaseModel):
    """Update Manager (Fase 9) — preparado; nunca despliega sin aprobación."""

    enabled: bool = True
    channel: str = "stable"  # stable | beta
    # Manifiesto local/remoto con la última versión disponible.
    manifest_path: Path = _PROJECT_ROOT / "data" / "production" / "update_manifest.json"
    auto_apply: bool = False  # jamás en producción sin aprobación humana


class LicenseSettings(BaseModel):
    """Licencias (Fase 9) — estructura preparada, sin restricciones activas."""

    enabled: bool = False  # sin restricciones todavía (por diseño)
    key: SecretStr = SecretStr("")
    license_path: Path = _PROJECT_ROOT / "data" / "production" / "license.json"


class MaintenanceSettings(BaseModel):
    """Mantenimiento programado y limpieza (Fase 9)."""

    enabled: bool = True
    # Limpieza periódica de temporales/spills antiguos.
    cleanup_interval_seconds: float = 86_400.0
    temp_max_age_hours: float = 168.0  # 7 días
    cleanup_dirs: list[Path] = Field(default_factory=lambda: [_PROJECT_ROOT / "data" / "spill"])


class FailoverSettings(BaseModel):
    """Alta disponibilidad por arriendo de líder sobre fichero compartido.

    Sólo el nodo que sostiene el arriendo fresco opera; un standby toma el
    relevo cuando el arriendo caduca. Sin infraestructura externa: un fichero
    compartido (NFS/volumen) basta para un primario + standby.
    """

    enabled: bool = False  # activarlo requiere un almacén compartido
    node_id: str = ""  # vacío → se deriva del hostname
    lease_path: Path = _PROJECT_ROOT / "data" / "production" / "leader.lease"
    lease_ttl_seconds: float = 30.0
    renew_interval_seconds: float = 10.0


class ImprovementSettings(BaseModel):
    """Continuous Improvement Engine (Fase 9, modo desarrollo permanente).

    El proyecto nunca se considera "terminado": este motor analiza el código y
    el rendimiento en busca de mejoras, y produce una lista priorizada.
    """

    enabled: bool = True
    scan_dir: Path = _PROJECT_ROOT / "app"
    interval_seconds: float = 604_800.0  # análisis semanal
    large_module_loc: int = 400  # LOC por encima → candidato a refactor
    duplicate_block_lines: int = 6  # bloque mínimo para marcar duplicado
    report_to_notion: bool = True


class ProductionSettings(BaseModel):
    """Capa de producción (Fase 9): live gating, safe mode, kill switch."""

    enabled: bool = False
    # Llave maestra. Con esto en False el Live Gate no puede aprobar jamás,
    # pase lo que pase con el resto de criterios.
    allow_live: bool = False
    operator: str = ""  # actor por defecto de las acciones auditadas
    state_dir: Path = _PROJECT_ROOT / "data" / "production"
    audit_path: Path = _PROJECT_ROOT / "logs" / "audit.jsonl"
    approval_path: Path = _PROJECT_ROOT / "data" / "production" / "live_approval.json"
    live: LiveGateSettings = Field(default_factory=LiveGateSettings)
    safe_mode: SafeModeSettings = Field(default_factory=SafeModeSettings)
    kill_switch: KillSwitchSettings = Field(default_factory=KillSwitchSettings)
    recovery: RecoverySettings = Field(default_factory=RecoverySettings)
    reporting: ReportingSettings = Field(default_factory=ReportingSettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)
    licenses: LicenseSettings = Field(default_factory=LicenseSettings)
    maintenance: MaintenanceSettings = Field(default_factory=MaintenanceSettings)
    failover: FailoverSettings = Field(default_factory=FailoverSettings)
    improvement: ImprovementSettings = Field(default_factory=ImprovementSettings)


class StrategyGeneratorSettings(BaseModel):
    """Generador automático de estrategias (Fase 10).

    No genera código aleatorio: combina bloques de señal e indicadores con
    filtros de contexto siguiendo reglas cuantitativas, con semilla reproducible.
    """

    max_blocks: int = 3  # bloques de señal combinados por estrategia
    min_blocks: int = 1
    max_filters: int = 2  # filtros de contexto por estrategia
    population: int = 24  # genomas propuestos por lote
    allow_short: bool = True
    random_seed: int = 7


class FeatureLabSettings(BaseModel):
    """Laboratorio de features: cada feature se valida antes de usarse."""

    forward_horizon: int = 5  # velas hacia delante para el retorno objetivo
    min_coverage: float = 0.90  # fracción mínima de valores finitos
    min_variance: float = 1e-9  # varianza mínima (rechaza constantes)
    min_abs_ic: float = 0.02  # |information coefficient| mínimo vs retorno futuro


class FactorLabSettings(BaseModel):
    """Investigación de factores estilo fondo cuantitativo (Fase 10)."""

    forward_horizon: int = 5
    min_abs_ic: float = 0.02
    top_k: int = 12  # factores retenidos en el ranking


class MultiObjectiveSettings(BaseModel):
    """Optimización multiobjetivo (Fase 10).

    Nunca optimiza sólo Profit Factor: escalariza varios objetivos y conserva el
    frente de Pareto. Los pesos son configurables; los objetivos ``max_*`` suman
    y ``drawdown`` resta (menos es mejor).
    """

    # Claves reales del QuantStatistics; los objetivos ``*_pct`` se normalizan
    # dividiendo entre 100 al escalarizar (drawdown menos = mejor). ``stability``
    # se incluye cuando el evaluador enriquece las métricas (pipeline/ranking).
    objectives: dict[str, float] = Field(
        default_factory=lambda: {
            "profit_factor": 1.0,
            "sharpe": 1.0,
            "expectancy_r": 0.75,
            "sqn": 0.5,
            "max_drawdown_pct": -0.5,
        }
    )
    population_size: int = 20
    generations: int = 12
    mutation_rate: float = 0.15
    random_seed: int = 7


class BayesianLabSettings(BaseModel):
    """Optimización bayesiana (Fase 10).

    Estimador de Parzen estructurado (TPE) sin dependencias externas: divide el
    historial en buenos/malos por cuantil y muestrea donde la razón de densidades
    es mayor. Guarda el historial y compara resultados entre corridas.
    """

    max_evaluations: int = 60
    n_startup: int = 12  # arranque aleatorio antes de activar el modelo
    gamma: float = 0.25  # cuantil que separa buenos de malos
    candidate_pool: int = 48  # candidatos puntuados por el surrogate en cada paso
    random_seed: int = 7


class SimulationClusterSettings(BaseModel):
    """Clúster de simulación: evalúa muchos backtests en paralelo (Fase 10)."""

    max_workers: int = 4
    chunk_size: int = 8


class CandidatePipelineSettings(BaseModel):
    """Etapas obligatorias antes de que una estrategia sea candidata (Fase 10).

    Backtesting → Walk Forward → Monte Carlo → Validación ML → Benchmark → Risk
    Review. Reutiliza el laboratorio de la Fase 6; no habilita live trading.
    """

    require_walk_forward: bool = True
    require_monte_carlo: bool = True
    require_benchmark: bool = True
    require_ml_review: bool = False  # asesor; off por defecto (necesita historial)
    require_risk_review: bool = True
    # Método del optimizador in-sample del walk-forward. Los espacios del
    # laboratorio son rangos continuos, así que 'grid' no aplica: random/genetic.
    walk_forward_method: str = "random"


class PaperValidationSettings(BaseModel):
    """Validación en paper antes de considerar producción (Fase 10).

    Ninguna estrategia se promueve de inmediato: acumula un período configurable
    en paper trading y debe cumplir mínimos de evidencia.
    """

    min_days: float = 7.0  # período mínimo en paper
    min_trades: int = 20
    min_profit_factor: float = 1.30
    max_drawdown_pct: float = 15.0


class ShadowModeSettings(BaseModel):
    """Shadow Mode (mejora obligatoria de la Fase 10).

    La estrategia experimental recibe los mismos datos que la vigente, simula
    operaciones y se compara estadísticamente. Nunca envía órdenes ni toca el
    Decision Engine.
    """

    min_signals: int = 30  # señales mínimas antes de concluir
    significance: float = 0.05  # p-valor para significancia estadística
    min_effect_r: float = 0.05  # ventaja mínima de expectativa (R) para declararla mejor


class PromotionSettings(BaseModel):
    """Promotion Manager fail-closed (Fase 10).

    Sólo promueve si se cumplen todos los requisitos, no hay drift, supera a la
    vigente y el operador aprueba. Toda decisión queda registrada.
    """

    require_operator_approval: bool = True
    require_beat_current: bool = True
    max_drift: float = 0.25  # techo tipo PSI de deriva de features
    min_improvement: float = 0.05  # mejora mínima del objetivo vs la vigente


class ResearchSettings(BaseModel):
    """Quant Research Lab (Fase 10): investiga, valida y promueve estrategias.

    Regla de oro: es un laboratorio **independiente de producción**. Nunca opera,
    nunca modifica estrategias en producción (trabaja sobre copias) y nunca
    habilita live trading. Descubre oportunidades y mejora el sistema con
    evidencia; la promoción final siempre exige aprobación humana.
    """

    enabled: bool = False
    state_dir: Path = _PROJECT_ROOT / "data" / "research"
    experiments_dir: Path = _PROJECT_ROOT / "data" / "research" / "experiments"
    knowledge_dir: Path = _PROJECT_ROOT / "data" / "research" / "knowledge"
    candidates_dir: Path = _PROJECT_ROOT / "data" / "research" / "candidates"
    reports_dir: Path = _PROJECT_ROOT / "data" / "research" / "reports"
    bayesian_dir: Path = _PROJECT_ROOT / "data" / "research" / "bayesian"
    # Objetivo primario para ordenar/comparar (además del multiobjetivo).
    objective: str = "sharpe"
    persist: bool = True
    generator: StrategyGeneratorSettings = Field(default_factory=StrategyGeneratorSettings)
    feature_lab: FeatureLabSettings = Field(default_factory=FeatureLabSettings)
    factor_lab: FactorLabSettings = Field(default_factory=FactorLabSettings)
    multi_objective: MultiObjectiveSettings = Field(default_factory=MultiObjectiveSettings)
    bayesian: BayesianLabSettings = Field(default_factory=BayesianLabSettings)
    simulation: SimulationClusterSettings = Field(default_factory=SimulationClusterSettings)
    pipeline: CandidatePipelineSettings = Field(default_factory=CandidatePipelineSettings)
    paper: PaperValidationSettings = Field(default_factory=PaperValidationSettings)
    shadow: ShadowModeSettings = Field(default_factory=ShadowModeSettings)
    promotion: PromotionSettings = Field(default_factory=PromotionSettings)


class Settings(BaseSettings):
    """Configuración raíz del sistema, compuesta por secciones."""

    model_config = SettingsConfigDict(
        env_prefix="QE_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "quant-engine"
    version: str = "0.6.0"

    trading: TradingSettings = Field(default_factory=TradingSettings)
    market: MarketSettings = Field(default_factory=MarketSettings)
    quant: QuantSettings = Field(default_factory=QuantSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    ml: MLSettings = Field(default_factory=MLSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    paper: PaperSettings = Field(default_factory=PaperSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    backtesting: BacktestingSettings = Field(default_factory=BacktestingSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    watchdog: WatchdogSettings = Field(default_factory=WatchdogSettings)
    production: ProductionSettings = Field(default_factory=ProductionSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    research: ResearchSettings = Field(default_factory=ResearchSettings)


def _env_files(environment: Environment) -> tuple[Path, ...]:
    """Return env files in precedence order (later overrides earlier).

    ``.env`` aporta la base y los secretos locales; el overlay del ambiente
    (``config/<env>.env``) tiene la última palabra — así ``testing`` puede,
    por ejemplo, forzar la desactivación de notificaciones.
    """
    overlay = _PROJECT_ROOT / "config" / f"{environment.value}.env"
    dotenv = _PROJECT_ROOT / ".env"
    return (dotenv, overlay)


@functools.lru_cache(maxsize=4)
def get_settings(environment: Environment | None = None) -> Settings:
    """Build (and cache) the settings for an environment.

    Args:
        environment: Explicit environment; when ``None`` it is detected from
            the ``QE_ENVIRONMENT`` process variable.

    Returns:
        Fully validated :class:`Settings` instance.
    """
    env = environment or detect_environment()
    settings = Settings(_env_file=_env_files(env))  # type: ignore[call-arg]
    # El ambiente explícito manda sobre lo que digan los archivos.
    if environment is not None and settings.environment is not environment:
        settings = settings.model_copy(update={"environment": environment})
    return settings

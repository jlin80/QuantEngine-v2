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
    # AUC mínimo de la VALIDACIÓN CRUZADA, evaluado aparte del holdout. La puerta
    # usaba `max(holdout, cv)`, así que un modelo sobreajustado con holdout 0.663
    # y cv 0.463 (peor que el azar) se aprobaba ignorando la señal de la CV. La
    # CV es la estimación más honesta: si la suspende, el modelo no pasa.
    min_cv_auc: float = 0.52
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
    # Evaluaciones con evidencia NUEVA y degradada antes de desactivar. Una
    # evaluación sin operaciones nuevas no cuenta: si contara, el umbral
    # mediría horas transcurridas en vez de degradación sostenida.
    disable_after_periods: int = 5
    min_weight: float = 0.10
    max_weight: float = 2.00
    weight_smoothing: float = 0.50  # suavizado del ajuste de peso [0-1]
    # Aplicar de verdad el gobierno sobre el Strategy Engine (pesos del consenso
    # y activación). **Por defecto NO**: el aplicador escucha y audita lo que
    # habría hecho, sin tocar nada. Encenderlo es una decisión explícita tras
    # ver qué propone el MSM — arrancar gobernando de golpe puede desactivar
    # varias estrategias en pocas horas y dejar al consenso sin votos
    # suficientes para superar `min_score`, es decir, sin operar.
    # Nada de esto puede habilitar live: sólo mueve configuración.
    apply_governance: bool = False


class MLEraSettings(BaseModel):
    """Una "era" del historial, delimitada por un fix de ejecución conocido.

    Attributes:
        name: Identificador de la era.
        until: Fecha (ISO ``YYYY-MM-DD``, UTC, **exclusiva**) hasta la que llega.
        weight: Peso de sus operaciones en el entrenamiento. ``0.0`` las excluye.
        reason: Qué bug de ejecución contamina esta era.
    """

    name: str
    until: str
    weight: float = 1.0
    reason: str = ""


class MLDataQualitySettings(BaseModel):
    """Saneamiento del training set: no aprender de bugs ya arreglados.

    El problema que esto resuelve: si el modelo entrena sobre operaciones en las
    que el stop estaba mal calculado, el trailing apretaba mal o la salida por
    régimen cortaba la tesis antes de tiempo, **no aprende "esta señal es mala"
    — aprende "esta señal es mala porque la ejecución la saboteó"**. Eso penaliza
    contextos que sí tenían edge y hace al modelo más torpe, no más listo.

    Las operaciones se clasifican por era usando su **hora de entrada**: una
    operación abierta antes de un fix corrió bajo las reglas viejas durante casi
    toda su vida, aunque cerrara después. Es la clasificación conservadora.
    """

    enabled: bool = True
    # Eras en orden cronológico. Una operación pertenece a la PRIMERA era cuya
    # fecha `until` no haya alcanzado; lo posterior a todas ellas es historial
    # limpio y pesa `clean_weight`.
    eras: list[MLEraSettings] = Field(
        default_factory=lambda: [
            MLEraSettings(
                name="pre_contract_size_y_familias_regimen",
                until="2026-07-27",
                weight=0.0,
                reason=(
                    "Stop mal calculado (bug de contract_size) y salida por régimen "
                    "comparando las 8 etiquetas en vez de familias. El R de estas "
                    "operaciones no mide la señal: mide un stop equivocado. No es "
                    "una muestra floja, es una medición inválida — se excluyen."
                ),
            ),
            MLEraSettings(
                name="pre_trailing_activate_r",
                until="2026-07-29",
                weight=0.35,
                reason=(
                    "El trailing apretaba el stop nada más abrir (sin gate de +1R), "
                    "liquidando posiciones por ruido. El sesgo es real pero acotado "
                    "y direccional: se conservan con peso reducido en vez de tirar "
                    "la muestra, porque la entrada y su contexto siguen siendo "
                    "válidos."
                ),
            ),
        ]
    )
    clean_weight: float = 1.0
    # Etiqueta de CALIDAD DE SEÑAL: excluye las operaciones cuyo cierre lo
    # decidió la ejecución (régimen, tiempo, kill switch, manual...). Esa
    # operación nunca llegó a poner a prueba su propia tesis, así que etiquetarla
    # como "señal mala" es exactamente el error que este bloque evita.
    signal_label_exit_reasons: list[str] = Field(
        default_factory=lambda: ["take_profit", "stop_loss", "trailing_stop", "break_even"]
    )


class MLExecutionRulesCheckSettings(BaseModel):
    """Gate de vigencia del modelo frente a las reglas de ejecución.

    Comprueba que el modelo activo se entrenó con las reglas vigentes
    (holding, sizing, filtros de riesgo, trailing) y avisa si no. **Avisa una
    vez por situación**, no una por vuelta: el estado dura hasta que un humano
    reentrena, y repetirlo cada hora lo convierte en ruido que se silencia.
    """

    enabled: bool = True
    check_interval_seconds: float = 3600.0


class MLCalibrationSettings(BaseModel):
    """Confidence Calibration Engine (Bloque 10): confianza declarada vs real.

    La correccion esta acotada a proposito: sin techo, una racha de 60
    operaciones puede producir un factor de 0.4 que apagaria medio sistema
    (ADR-109).
    """

    enabled: bool = True
    window: int = 5_000
    bins: int = 10
    min_sample: int = 100
    # Un tramo con tres observaciones da una tasa de 0.0 o 0.67 y arrastra el
    # ECE con ruido que no significa nada.
    min_bin_sample: int = 10
    min_correction: float = 0.7
    max_correction: float = 1.3


class MLImportanceSettings(BaseModel):
    """Feature Importance Tracker (Bloque 13): importancia por permutacion.

    No se usa SHAP y esta razonado en ADR-112: exigiria una dependencia binaria
    pesada que solo cubriria parte del catalogo de modelos, y habria que caer a
    otra metrica para el resto — dos numeros distintos llamados igual.
    """

    enabled: bool = True
    # Repeticiones por feature: con un solo barajado, features irrelevantes
    # salen "importantes" por puro azar del reparto.
    repeats: int = 5
    min_sample: int = 50
    history_limit: int = 50
    # Semilla fija: sin ella, dos mediciones del mismo modelo dan numeros
    # distintos y el "cambio" mediria el ruido del metodo.
    seed: int = 20260805


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
    calibration: MLCalibrationSettings = Field(default_factory=MLCalibrationSettings)
    importance: MLImportanceSettings = Field(default_factory=MLImportanceSettings)
    model: MLModelSettings = Field(default_factory=MLModelSettings)
    validation: MLValidationSettings = Field(default_factory=MLValidationSettings)
    drift: MLDriftSettings = Field(default_factory=MLDriftSettings)
    advisor: MLAdvisorSettings = Field(default_factory=MLAdvisorSettings)
    meta: MLMetaStrategySettings = Field(default_factory=MLMetaStrategySettings)
    data_quality: MLDataQualitySettings = Field(default_factory=MLDataQualitySettings)
    execution_rules_check: MLExecutionRulesCheckSettings = Field(
        default_factory=MLExecutionRulesCheckSettings
    )


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
    # Fidelidad del laboratorio: si está activo, el backtest construye el
    # ExecutionEngine con el Market Context real (régimen, sesión, volatilidad)
    # en vez de `context=None`. Con `None`, `regime` llega siempre "unknown" y la
    # salida por cambio de régimen —el 72 % de los cierres en producción— **no
    # existe** en el backtest: laboratorio y producción son sistemas distintos.
    # Se deja como toggle, y no cableado a fuego, para poder medir cuánto cambia
    # el resultado al añadir esa salida (misma razón que `ml.data_quality.enabled`).
    market_context_enabled: bool = True
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
    # Umbrales de clasificación de volatilidad, como % del precio.
    #
    # Los valores anteriores (0.05 / 0.80) estaban calibrados para una escala
    # temporal mucho mayor y en 1m no clasificaban NADA: medido sobre 1187
    # operaciones reales, el ATR% máximo observado fue 0.261 % — es decir, el
    # umbral HIGH de 0.80 % era inalcanzable por construcción y `volatility`
    # llegaba constante a `normal` en el 100 % de las operaciones. Una variable
    # constante no informa a nadie: ni al `ConfidenceEngine`, ni a los filtros,
    # ni al ML (que la recibía como feature muerta).
    #
    # Los valores nuevos salen de la distribución real en 1m (p≈25 y p≈85).
    atr_pct_low: float = 0.04
    atr_pct_high: float = 0.12
    # Los umbrales por símbolo existen porque la escala **no es comparable entre
    # activos**: la mediana de ATR% en 1m es 0.038 % en oro y 0.068 % en ETH, así
    # que un único par de umbrales marcaría al oro como LOW casi siempre y a ETH
    # casi nunca. Resolución en dos escalones: por símbolo → global.
    atr_pct_low_by_symbol: dict[str, float] = Field(
        default_factory=lambda: {
            "BTCUSDM": 0.04,
            "ETHUSDM": 0.05,
            "USTECM": 0.04,
            "XAUUSDM": 0.03,
        }
    )
    atr_pct_high_by_symbol: dict[str, float] = Field(
        default_factory=lambda: {
            "BTCUSDM": 0.12,
            "ETHUSDM": 0.14,
            "USTECM": 0.13,
            "XAUUSDM": 0.07,
        }
    )
    stale_data_seconds: float = 30.0

    def atr_pct_low_for(self, symbol: str) -> float:
        """Umbral LOW del símbolo, con el global como fallback.

        Args:
            symbol: Símbolo (se normaliza a mayúsculas).

        Returns:
            El umbral aplicable.
        """
        return self.atr_pct_low_by_symbol.get(symbol.upper(), self.atr_pct_low)

    def atr_pct_high_for(self, symbol: str) -> float:
        """Umbral HIGH del símbolo, con el global como fallback.

        Args:
            symbol: Símbolo (se normaliza a mayúsculas).

        Returns:
            El umbral aplicable.
        """
        return self.atr_pct_high_by_symbol.get(symbol.upper(), self.atr_pct_high)

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
            # Bloque 3. Fail-open: sin libro del proveedor no mide y deja pasar,
            # asi que activarlo por defecto no cambia nada con MT5.
            "microstructure",
            # Bloque 7. Fail-open por debajo del minimo de dimensiones
            # observables: no bloquea por ignorancia.
            "position_quality",
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
    # Resultado virtual **por señal** (Bloque 8). El snapshot de arriba guarda
    # el agregado por estrategia, que no permite unir fila a fila con el Trade
    # Journal: sin esto, la calidad de la señal sólo se puede aproximar desde
    # el motivo de salida de la operación ejecutada.
    outcomes_path: Path = _PROJECT_ROOT / "data" / "performance" / "virtual_outcomes.jsonl"
    persist_outcomes: bool = True
    outcomes_flush_size: int = 50


class QuantEdgeHealthWeights(BaseModel):
    """Pesos del resumen 0-100 de salud del edge.

    Son un juicio, no una medida: por eso son configurables y por eso el
    informe conserva siempre las métricas individuales y las razones.
    """

    expectancy: float = 0.4
    stability: float = 0.2
    persistence: float = 0.2
    decay: float = 0.2


class QuantEdgeResearchSettings(BaseModel):
    """Edge Research Engine (Bloque 1): salud del edge por estrategia.

    Mide sobre una **ventana rodante**, no sobre el acumulado: el acumulado no
    olvida, y una estrategia que dejó de funcionar hace semanas sigue
    presentando buen aspecto durante mucho tiempo.

    Este motor no opera ni desactiva nada por sí solo: produce evidencia.
    """

    enabled: bool = True
    cycle_interval_seconds: float = 900.0
    # Ventana rodante por estrategia. 300 resoluciones cubren varios días de
    # scalping sin arrastrar régimenes ya extintos.
    rolling_window: int = 300
    blocks: int = 6
    min_sample: int = 30
    seen_limit: int = 50_000
    # Umbrales de clasificación. `degrading` exige DOS motivos concurrentes:
    # una sola métrica fuera de rango es ruido con muestras de este tamaño.
    decay_threshold: float = 0.05
    min_stability: float = 0.35
    min_persistence: float = 0.5
    degrading_reasons: int = 2
    # Suelo del multiplicador expuesto al Meta Strategy Manager: aunque la
    # salud sea 0, este motor nunca puede anular una estrategia por sí mismo.
    factor_floor: float = 0.5
    health_weights: QuantEdgeHealthWeights = Field(default_factory=QuantEdgeHealthWeights)
    history_path: Path = _PROJECT_ROOT / "data" / "performance" / "edge_reports.jsonl"
    persist_history: bool = True
    history_memory_limit: int = 200


class QuantAttributionSettings(BaseModel):
    """Edge Attribution Engine (Bloque 2): por qué ganó o perdió cada operación.

    Mide **asociación histórica, no causa**: agrupa cada factor en buckets y
    compara la R media de cada bucket con la global. Los factores están
    correlacionados entre sí, así que sus aportes no son aditivos — por eso el
    informe reporta siempre el residuo.
    """

    enabled: bool = True
    cycle_interval_seconds: float = 900.0
    # Muestra mínima del join operación↔foto de factores. Por debajo no se
    # publica explicación: sólo el desglose de por qué no la hay.
    min_sample: int = 40
    # Un bucket con dos operaciones produce un lift enorme y sin significado, y
    # en un dashboard esa fila sube arriba del todo justo por ser ruido.
    min_bucket: int = 8
    max_trades: int = 5_000
    label_factors: tuple[str, ...] = (
        "strategy",
        "strategy_category",
        "regime",
        "volatility",
        "session",
    )
    snapshots_path: Path = _PROJECT_ROOT / "data" / "performance" / "factor_snapshots.jsonl"
    persist_snapshots: bool = True
    snapshots_flush_size: int = 25


class QuantMicrostructureSettings(BaseModel):
    """Microstructure Engine (Bloque 3): dinámica interna del libro.

    **Exige libro de órdenes incremental.** MT5 no lo publica, así que con el
    bróker de la demo el motor reporta `observable=False` y todas las métricas
    en `None`. Ver ADR-102 y `docs/orderflow_nativo.md`.
    """

    enabled: bool = True
    window_seconds: float = 60.0
    min_updates: int = 20
    min_elapsed_seconds: float = 1.0
    max_events_per_symbol: int = 20_000
    depth_levels: int = 5
    # Nocional de referencia del impacto estimado. No es el tamaño real de la
    # orden: es una vara de medir fija para que la serie sea comparable en el
    # tiempo aunque el sizing cambie.
    impact_notional: float = 10_000.0
    # Reposición considerada "sana" (1.0 = el libro repone lo que se le come).
    resiliency_reference: float = 1.0
    # Veto por presión de ejecución. Fail-open por diseño: sin libro no hay
    # medición, y un filtro que bloquea por falta de datos apagaría el motor
    # entero con el bróker actual.
    max_execution_pressure: float = 0.85


class QuantRegimeForecastSettings(BaseModel):
    """Regime Forecast Engine (Bloque 4): probabilidad del próximo desenlace.

    Método: frecuencia condicional empírica. Con la muestra de este proyecto,
    un modelo más rico produciría parámetros peor estimados y un número más
    difícil de auditar. Lo que hace útil al bloque no es el pronóstico sino su
    validación contra el pronóstico trivial (ADR-103).
    """

    enabled: bool = True
    horizon_bars: int = 20
    min_sample: int = 50
    # Muestra a la que la confianza satura. Mide cuánta evidencia hay detrás,
    # NO si el pronóstico acierta — eso lo dice el Brier score.
    confidence_sample: int = 500
    # Suavizado de Laplace: sin él, un desenlace nunca visto tendría
    # probabilidad 0 y el motor afirmaría que algo es imposible por no haberlo
    # visto en unos cientos de observaciones.
    smoothing: float = 1.0
    validation_window: int = 2_000
    max_pending: int = 500


class QuantCorrelationSettings(BaseModel):
    """Correlation Intelligence (Bloque 5): relaciones entre activos.

    Todo se mide sobre **rendimientos**, no precios: dos precios que suben dan
    correlación alta aunque no tengan nada que ver, porque la tendencia común
    domina el cálculo.
    """

    enabled: bool = True
    cycle_interval_seconds: float = 900.0
    window: int = 500
    min_sample: int = 100
    # Vida media de la correlación dinámica, en observaciones. Frente a una
    # ventana fija, evita el borde duro: una observación no pasa de pesar todo
    # a pesar nada por haber salido de la ventana.
    ewma_halflife: float = 50.0
    max_lag: int = 10
    # Umbral para que el filtro considere dos símbolos correlacionados.
    min_correlation: float = 0.7
    # Un lag "ganador" con correlación de 0.05 es ruido con signo: sumarlo al
    # ranking de liderazgo lo convertiría en una lotería.
    min_lead_correlation: float = 0.3


class ExecutionOptimizerSettings(BaseModel):
    """Execution Optimizer (Bloque 6): IOC vs LIMIT vs MARKET.

    El parámetro que gobierna todo es `miss_cost_bps`: cuánto cuesta **no**
    entrar. Sin él, LIMIT gana siempre —es el más barato cuando se llena— y el
    optimizador se convierte en una máquina de no operar. Es una política, no
    una medida (ADR-105).
    """

    enabled: bool = True
    # Probabilidad base de llenado. Se corrige con el desequilibrio del libro
    # sólo cuando ese libro es observable (Bloque 3).
    limit_fill_base: float = 0.55
    ioc_fill_base: float = 0.8
    imbalance_coeff: float = 0.3
    miss_cost_bps: float = 12.0
    quality_reference_bps: float = 20.0


class QuantPositionQualitySettings(BaseModel):
    """Position Quality Engine (Bloque 7): puede vetar operaciones.

    Puntúa la **posición** que saldría de la decisión, no la señal. Es un veto
    independiente del score y por eso vive en su propio motor (ADR-106).
    """

    enabled: bool = True
    min_score: float = 45.0
    # Fail-open por debajo de este mínimo de evidencia: bloquear con dos
    # dimensiones observables sería bloquear por ignorancia.
    min_dimensions: int = 4
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "setup": 1.5,
            "execution": 1.0,
            "risk": 1.0,
            "liquidity": 1.2,
            "context": 1.0,
            "cost": 1.2,
        }
    )
    # Suelos por dimensión: promediar deja que una liquidez pésima se esconda
    # detrás de un setup excelente, y esa es justo la posición que duele.
    dimension_floors: dict[str, float] = Field(
        default_factory=lambda: {"liquidity": 0.2, "cost": 0.15}
    )
    block_on_floor_breach: bool = True
    max_cost_bps: float = 30.0


class PortfolioIntelligenceSettings(BaseModel):
    """Portfolio Intelligence (Bloque 8): desglose del PnL realizado.

    Mide de donde SALIO el dinero, no donde esta el riesgo ahora: son preguntas
    distintas y este modulo solo responde la primera (ADR-107).
    """

    enabled: bool = True
    min_sample: int = 50
    max_trades: int = 20_000


class CostAttributionSettings(BaseModel):
    """Cost Attribution Engine (Bloque 9): reparto del bruto entre costes.

    El coste oculto es un **residuo**: si crece, significa que el sistema no
    esta midiendo algun coste, no que exista un concepto llamado oculto
    (ADR-108).
    """

    enabled: bool = True
    max_trades: int = 20_000
    # Fraccion del bruto por encima de la cual el residuo sin explicar deja de
    # ser ruido de redondeo y pasa a ser una alarma de contabilidad incompleta.
    hidden_alert_ratio: float = 0.1


class DataQualitySettings(BaseModel):
    """Data Quality Engine (Bloque 11): salud del dato -> multiplicador de riesgo.

    Reduce exposicion, nunca apaga: apagar por una metrica de calidad convierte
    un problema de datos en una parada total, y las paradas totales las decide
    el kill switch, que tiene auditoria propia (ADR-110).
    """

    enabled: bool = True
    cycle_interval_seconds: float = 60.0
    # Por debajo de este score se empieza a reducir exposicion.
    degraded_score: float = 70.0
    # Suelo del multiplicador: nunca llega a 0.
    risk_floor: float = 0.3
    signal_floor: float = 0.8
    # Senales CRITICAS con su suelo. Una sola de estas por los suelos degrada
    # aunque la media salve: promediar deja que un motor ciego (missing_data=0)
    # o un reloj filtrado (clock_drift=0) queden escondidos detras de siete
    # senales sanas — que es exactamente el fallo del 04/08.
    critical_signals: dict[str, float] = Field(
        default_factory=lambda: {
            "clock_drift": 0.5,
            "missing_data": 0.5,
            # `tick_quality` es critica porque el fallo del 04/08 fue
            # literalmente esto: el validador descartaba el 100% de los ticks y
            # la media de las demas senales lo habria tapado.
            "tick_quality": 0.5,
            "feed_quality": 0.5,
        }
    )
    max_timestamp_drift_seconds: float = 5.0
    max_clock_skew_seconds: float = 5.0
    max_exchange_lag_ms: float = 2_000.0
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "feed_quality": 1.5,
            "packet_loss": 1.0,
            "tick_quality": 1.2,
            "orderbook_quality": 0.8,
            "missing_data": 1.5,
            "timestamp_drift": 1.0,
            # El reloj pesa mas que nada: es la senal del incidente que dejo al
            # motor ciego cuatro dias sin un solo error en el log (ADR-091).
            "clock_drift": 2.0,
            "exchange_lag": 1.0,
        }
    )


class MetaRiskSettings(BaseModel):
    """Meta Risk Engine (Bloque 12): la salud de la maquina, no del mercado.

    Compone con la calidad del dato (Bloque 11) por PRODUCTO, no por minimo: un
    feed mediocre en una maquina saturada es peor que cualquiera de las dos
    cosas por separado (ADR-111).
    """

    enabled: bool = True
    cycle_interval_seconds: float = 60.0
    degraded_score: float = 70.0
    risk_floor: float = 0.3
    signal_floor: float = 0.8
    max_cpu_percent: float = 90.0
    max_memory_percent: float = 90.0
    # Por debajo de esta fraccion del limite, el recurso se considera sano del
    # todo. Sin zona de confort, una CPU al 20% frente a un techo del 90% daba
    # una senal de 0.78 y la maquina parecia siempre a medio gas — con lo que
    # la degradacion real no destacaba sobre el fondo.
    comfort_fraction: float = 0.5
    max_event_loop_lag_ms: float = 250.0
    max_event_bus_queue: int = 10_000
    tracked_components: list[str] = Field(
        default_factory=lambda: ["redis", "broker", "mt5", "exchange", "api", "scheduler", "cache"]
    )
    # Traduccion del estado del watchdog a senal 0-1. Un estado desconocido no
    # aparece aqui: se trata como NO observable, no como medio roto.
    status_scores: dict[str, float] = Field(
        default_factory=lambda: {"healthy": 1.0, "degraded": 0.5, "down": 0.0}
    )
    # Componentes cuya caida degrada por si sola, sin esperar a la media.
    critical_components: dict[str, float] = Field(
        default_factory=lambda: {
            "broker": 0.5,
            "event_loop": 0.5,
            # La CPU saturada es el degradador silencioso clasico: no impide
            # operar, impide operar A TIEMPO, y eso se lee como slippage.
            "cpu": 0.5,
        }
    )
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "cpu": 1.5,
            "memory": 1.0,
            "event_loop": 1.5,
            "event_bus": 1.0,
            "redis": 0.8,
            "broker": 2.0,
            "mt5": 1.5,
            "exchange": 1.0,
            "api": 0.5,
            "scheduler": 0.8,
            "cache": 0.5,
            "data_quality": 1.5,
        }
    )


class QuantRejectionsSettings(BaseModel):
    """Why Not Trade Engine (Bloque 14): registro estructurado de rechazos.

    El motor ya explicaba sus rechazos en texto. El texto sirve para leer UNA
    decision y para nada mas: no se puede agregar ni contar. Esto lo convierte
    en datos (ADR-113).
    """

    enabled: bool = True
    persist: bool = True
    path: Path = _PROJECT_ROOT / "data" / "engine" / "rejections.jsonl"
    flush_size: int = 50
    memory_limit: int = 2_000


class ShadowBenchmarkSettings(BaseModel):
    """Live Shadow Benchmark (Bloque 15): paper vs live vs fill ideal.

    Live trading permanece DESHABILITADO: este bloque no lo habilita ni lo
    prepara para habilitarse. El carril live se declara ausente, con su motivo,
    y el gap medido se etiqueta como linea base y no como medicion (ADR-114).
    """

    enabled: bool = True
    max_trades: int = 20_000
    # Por debajo de esta muestra no se emite ninguna recomendacion: una
    # recomendacion es una llamada a la accion, y emitirla sobre diez
    # operaciones es peor que callarse.
    min_sample: int = 100
    high_gap_bps: float = 15.0
    # Fraccion de la R media que se lleva la ejecucion antes de considerarlo un
    # problema de ejecucion y no de senal.
    high_edge_share: float = 0.3


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
    edge_research: QuantEdgeResearchSettings = Field(default_factory=QuantEdgeResearchSettings)
    attribution: QuantAttributionSettings = Field(default_factory=QuantAttributionSettings)
    rejections: QuantRejectionsSettings = Field(default_factory=QuantRejectionsSettings)
    microstructure: QuantMicrostructureSettings = Field(default_factory=QuantMicrostructureSettings)
    regime_forecast: QuantRegimeForecastSettings = Field(
        default_factory=QuantRegimeForecastSettings
    )
    correlation: QuantCorrelationSettings = Field(default_factory=QuantCorrelationSettings)
    position_quality: QuantPositionQualitySettings = Field(
        default_factory=QuantPositionQualitySettings
    )

    def strategy_settings(self, name: str) -> QuantStrategySettings:
        """Config de una estrategia (defaults si no está declarada)."""
        return self.strategies.get(name, QuantStrategySettings())


class HealthSettings(BaseModel):
    """Health Monitor."""

    check_interval_seconds: float = 30.0
    cpu_warn_pct: float = 85.0
    memory_warn_pct: float = 85.0
    disk_warn_pct: float = 90.0
    # Desviación tolerada entre el reloj efectivo del proceso y el de pared.
    # En vivo debe ser 0: cualquier valor apreciable significa que un reloj
    # simulado de backtest se filtró fuera de su contexto, y con el reloj mal
    # el validador de mercado descarta todos los ticks y el motor deja de
    # operar en silencio. Por eso el umbral es bajo y el estado UNHEALTHY.
    max_clock_skew_seconds: float = 5.0


class PipelineWatchSettings(BaseModel):
    """Vigilancia del pipeline: detecta que el motor dejó de operar en silencio.

    Nace de un incidente real: un reloj simulado filtrado dejó al validador
    descartando el **100% de los ticks** durante 4 días. El motor no estaba
    caído —respondía a la API, los servicios estaban "running", el watchdog no
    veía nada raro— simplemente había dejado de ver el mercado. Ninguna métrica
    existente lo reflejaba.

    Estas dos alarmas no vigilan *aquella* causa (de eso ya se encarga
    ``health.max_clock_skew_seconds``), sino el **efecto**: da igual qué lo
    provoque, si el motor se queda ciego o mudo hay que enterarse el primer día.

    - **Ciego**: entran datos pero se descartan casi todos.
    - **Mudo**: entran datos limpios y aun así no sale ninguna señal.

    Se mide por *deltas entre muestras*, no sobre contadores acumulados: un
    acumulado diluye el presente y, tras un incidente largo, seguiría en rojo
    mucho después de haberse recuperado.
    """

    enabled: bool = True
    check_interval_seconds: float = 300.0
    # --- Alarma "ciego" ---
    # Ticks revisados mínimos en la ventana para que el ratio signifique algo.
    min_samples: int = 50
    # Fracción de descarte que se considera ceguera. No se pone en 1.0: un 95%
    # sostenido ya es un motor que no opera, y esperar al 100% exacto es esperar
    # a que el caso sea perfecto.
    blind_discard_ratio: float = 0.95
    # --- Alarma "mudo" ---
    # Ticks limpios mínimos para afirmar que el mercado está vivo. Sin esto, un
    # mercado cerrado (sin datos y sin señales) dispararía la alarma cada noche
    # y acabaría ignorada.
    min_clean_samples: int = 200
    # Ventanas consecutivas con datos limpios y cero señales antes de avisar.
    # Con el intervalo por defecto son 30 minutos: por encima del hueco normal
    # entre señales, por debajo de "me he pasado la sesión sin operar".
    silent_windows: int = 6


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
    # Piso adicional: el stop nunca puede estar a menos de N veces el spread.
    # `min_stop_pct` es global, pero el spread varía mucho entre símbolos (XAU
    # ~0.56 bps, USTEC ~1.05, ETH ~3.03): con un 0.15% fijo el stop de ETH queda
    # a sólo 5× el spread y lo barre el ruido, mientras que en oro son 27×.
    # Con 8×, oro y USTEC no cambian (su piso porcentual ya es mayor) y sólo se
    # ensancha donde hacía falta.
    min_stop_spread_multiple: float = 8.0
    reward_risk: float = 1.5  # objetivo = riesgo × esta relación
    kelly_fraction: float = 0.25  # fracción parcial de Kelly
    max_position_pct: float = 20.0  # tope de notional como % del equity
    min_quantity: float = 0.0
    # Overrides por símbolo (mismo patrón que `atr_pct_low_by_symbol`): tanto
    # el riesgo por operación como el tope de notional dependen del precio por
    # unidad del subyacente, y ese precio no es comparable entre símbolos. El
    # lote mínimo de XAUUSD (contract_size=100, ~4300 USD/onza) vale ~10x más
    # que el de BTC/ETH/USTEC (contract_size=1) al mismo tamaño de cuenta: un
    # único par de porcentajes deja a oro sin poder abrir ni el lote mínimo, o
    # afloja la protección del resto de símbolos si se sube el global para
    # que oro quepa. `max_position_pct` en particular es DELIBERADAMENTE
    # independiente del apalancamiento (protege contra el movimiento de
    # precio, no contra el margen requerido); el override es por símbolo, no
    # por apalancamiento.
    risk_per_trade_pct_by_symbol: dict[str, float] = Field(default_factory=dict)
    max_position_pct_by_symbol: dict[str, float] = Field(default_factory=dict)

    def risk_per_trade_pct_for(self, symbol: str) -> float:
        """Riesgo por operación del símbolo, con el global como fallback."""
        return self.risk_per_trade_pct_by_symbol.get(symbol.upper(), self.risk_per_trade_pct)

    def max_position_pct_for(self, symbol: str) -> float:
        """Tope de notional del símbolo, con el global como fallback."""
        return self.max_position_pct_by_symbol.get(symbol.upper(), self.max_position_pct)


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
    # Overrides por símbolo, mismo patrón y misma razón que
    # `SizingSettings.max_position_pct_by_symbol`: el notional de un lote
    # mínimo escala con el contract_size y el precio por unidad del
    # subyacente, que no es comparable entre XAUUSD (contract_size=100,
    # miles de USD/onza) y BTC/ETH/USTEC (contract_size=1). Un único par de
    # porcentajes globales o bloquea a oro incluso en su lote mínimo, o
    # afloja la protección de correlación/exposición del resto de símbolos si
    # se sube lo suficiente como para que oro quepa.
    max_symbol_exposure_pct_by_symbol: dict[str, float] = Field(default_factory=dict)
    max_correlation_exposure_pct_by_symbol: dict[str, float] = Field(default_factory=dict)
    correlation_groups: list[list[str]] = Field(default_factory=list)
    min_liquidity: float = 0.0  # volumen reciente mínimo
    max_spread_bps: float = 10.0
    # Circuit breaker: pérdida máxima dentro de una ventana móvil.
    circuit_breaker_loss_pct: float = 5.0
    circuit_breaker_window_minutes: float = 15.0
    # Kill switch: drawdown máximo tolerado sobre el equity pico.
    kill_switch_drawdown_pct: float = 20.0

    def max_symbol_exposure_pct_for(self, symbol: str) -> float:
        """Tope de exposición del símbolo, con el global como fallback."""
        return self.max_symbol_exposure_pct_by_symbol.get(
            symbol.upper(), self.max_symbol_exposure_pct
        )

    def max_correlation_exposure_pct_for(self, symbol: str) -> float:
        """Tope de exposición correlacionada del símbolo, con el global como fallback."""
        return self.max_correlation_exposure_pct_by_symbol.get(
            symbol.upper(), self.max_correlation_exposure_pct
        )


class StrategyExperimentSettings(BaseModel):
    """Experimentos con fecha de corte por estrategia (decisión asistida).

    Una estrategia con R negativo persistente no debería depender de que el
    operador se acuerde de revisarla. Se abre un experimento con **fecha de
    corte**: al vencer, se mide su expectativa con las operaciones cerradas
    *desde que empezó el experimento* y, si sigue en negativo con muestra
    suficiente, el sistema la marca como **candidata a desactivación** y avisa
    por Discord con los números.

    Regla dura: esto **nunca** desactiva nada. Sólo propone. Apagar una
    estrategia es mover ``execution.strategies_enabled`` a mano.
    """

    enabled: bool = True
    # Estrategias bajo observación. El experimento arranca la primera vez que
    # el job corre con la estrategia en esta lista, y su inicio se persiste,
    # así que un reinicio del motor no reinicia el reloj.
    watching: list[str] = Field(default_factory=lambda: ["atr_expansion", "mean_reversion"])
    # Ventana del experimento. La tarea pedía 48-72h tras el cambio de holding
    # del Bloque 1; se toma el extremo largo para no juzgar con muestra corta.
    deadline_hours: float = 72.0
    # Operaciones cerradas mínimas dentro de la ventana para emitir veredicto.
    # Por debajo de esto el veredicto es "muestra insuficiente" y la ventana se
    # extiende: juzgar con 3 operaciones sería ruido, no evidencia.
    min_trades: int = 20
    # Cuánto se extiende la ventana cuando la muestra no alcanza.
    extension_hours: float = 24.0
    # Expectativa (en R) por debajo de la cual se propone la desactivación.
    max_expectancy_r: float = 0.0
    # Cadencia del chequeo. Barato: sólo lee el Trade Journal en memoria.
    check_interval_seconds: float = 3600.0
    state_path: Path = _PROJECT_ROOT / "data" / "execution" / "strategy_experiments.jsonl"
    persist: bool = True


class FalsificationSettings(BaseModel):
    """Falsación automática del cambio de holding por estrategia (Bloque 7.1).

    "Se desplegó sin errores" no es evidencia de que el cambio funcione. El
    Bloque 1 hizo una predicción concreta y comprobable, y esto la mide sola en
    la ventana posterior al cambio:

    - ``take_profit`` debe **subir del 0 %**: si ninguna operación llega al
      objetivo, el holding sigue cortando la tesis antes de tiempo.
    - ``regime_change`` debe **bajar del 80 %**: era el síntoma original.
    - La **duración mediana** debe acercarse a la esperada por estrategia.

    El veredicto se notifica por Discord, falle o acierte. Un cambio que no se
    verifica no se distingue de uno que no se hizo.
    """

    enabled: bool = True
    # Ventana de medición tras el cambio. La tarea pedía 24-48h; se toma el
    # extremo largo para no juzgar con muestra corta.
    window_hours: float = 48.0
    # Operaciones cerradas mínimas para emitir veredicto. Por debajo, la ventana
    # se extiende en vez de concluir con ruido.
    min_trades: int = 20
    extension_hours: float = 24.0
    # Criterios de éxito, tal como los enunció el bloque.
    min_take_profit_pct: float = 0.0  # estrictamente mayor que esto
    max_regime_change_pct: float = 80.0  # estrictamente menor que esto
    # Tolerancia de la duración mediana frente a la esperada por estrategia.
    duration_tolerance: float = 0.5  # ±50 % del umbral de holding aplicable
    check_interval_seconds: float = 3600.0
    state_path: Path = _PROJECT_ROOT / "data" / "execution" / "falsification.jsonl"
    persist: bool = True


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
    # El trailing sólo arranca tras +N R. Sin este gate, con ATR bajo (velas
    # desde ticks) `highest_price` arranca en la entrada y `entry - ATR×2` cae
    # MÁS CERCA que el stop inicial: el trailing aprieta el stop nada más abrir,
    # antes de que el precio se mueva, y liquida la posición por ruido.
    trailing_activate_r: float = 1.0
    # Nº de comprobaciones consecutivas con régimen adverso antes de cerrar.
    # El régimen 1m parpadea entre etiquetas; exigir confirmación evita cerrar
    # por un cambio de una sola lectura.
    regime_exit_confirmations: int = 2
    # Comparar FAMILIAS de régimen (continuación / reversión a la media /
    # adverso) en vez de las 8 etiquetas sueltas. Pasar de "breakout" a
    # "trending" estando largo no es motivo para salir: es la misma tesis.
    regime_exit_family_only: bool = True
    max_holding_minutes: float = 240.0  # salida por tiempo (0 desactiva)
    exit_on_regime_change: bool = True
    # Tiempo mínimo antes de que un cambio de régimen pueda cerrar: sin esto,
    # el régimen "parpadea" entre etiquetas vela a vela (más en cripto, velas
    # 1m ruidosas) y corta la posición casi al entrar, antes de que se mueva.
    regime_change_min_holding_seconds: float = 180.0
    # Holding mínimo POR ESTRATEGIA. Un valor global es un promedio que no le
    # sirve a nadie: `order_block` necesita ~30 min para resolver su tesis y
    # `bos` la resuelve en ~2 min. Con un único número, o se corta al primero
    # antes de tiempo o se deja al segundo colgado sin salida por régimen.
    # Los valores de arranque son la duración media medida por el evaluador
    # continuo (señales virtuales), redondeada. Las claves se comparan en
    # minúsculas. Lo no listado cae a la categoría y luego al valor global.
    regime_change_min_holding_by_strategy: dict[str, float] = Field(
        default_factory=lambda: {
            "order_block": 1950.0,
            "fair_value_gap": 880.0,
            "volume_profile": 900.0,
            "mean_reversion": 770.0,
            "atr_expansion": 150.0,
            "bos": 135.0,
            # `choch` NO lleva valor propio a propósito: su duración medida
            # (~4 s) era un artefacto del evaluador continuo, que resolvía las
            # señales contra la vela EN CURSO —cuyo rango incluye precio
            # anterior a la señal—. Corregido en esta misma entrega
            # (`PerformanceTracker.evaluate_open` ya sólo mira velas que
            # empiezan después de la entrada), pero hasta que haya muestra
            # nueva y limpia se queda en el fallback por categoría.
        }
    )
    # Segundo escalón del fallback: holding mínimo por categoría de estrategia
    # (la que declara cada plugin: smc / breakout / trend / mean_reversion /
    # orderflow / volume / volatility / momentum). Cubre a las estrategias sin
    # historial propio suficiente sin dejarlas en el valor global genérico.
    regime_change_min_holding_by_category: dict[str, float] = Field(
        default_factory=lambda: {
            "smc": 900.0,
            "volume": 900.0,
            "mean_reversion": 770.0,
            "trend": 600.0,
            "breakout": 300.0,
            "momentum": 300.0,
            "orderflow": 180.0,
            "volatility": 150.0,
        }
    )
    # Toggle de operativa por símbolo: {"XAUUSDM": false} deja de abrir posiciones
    # en ese símbolo sin sacarlo del feed de datos (sigue alimentando estrategias,
    # backtests y ML). Lo no listado se opera. Las claves se comparan en MAYÚSCULAS.
    # Pensado para símbolos cuyo lote mínimo no cabe en el equity actual (el oro
    # necesita ~20k con el tope de exposición al 20%).
    symbols_enabled: dict[str, bool] = Field(default_factory=dict)
    # Mismo toggle, pero por ESTRATEGIA: {"atr_expansion": false} deja de abrir
    # posiciones atribuidas a esa estrategia sin sacarla del motor — sigue
    # emitiendo señales, votando en el consenso y alimentando evaluación y ML,
    # así que se puede medir si habría mejorado sin haber perdido su historial.
    # Lo no listado se opera. Las claves se comparan en MINÚSCULAS.
    # Nadie lo modifica automáticamente: el experimento del Bloque 2 sólo
    # *propone* candidatas y avisa por Discord; apagarlas es decisión humana.
    strategies_enabled: dict[str, bool] = Field(default_factory=dict)
    report_interval_seconds: float = 3600.0  # resumen periódico a Discord
    journal_path: Path = _PROJECT_ROOT / "data" / "execution" / "journal.jsonl"
    persist_journal: bool = True
    commission: CommissionSettings = Field(default_factory=CommissionSettings)
    slippage: SlippageSettings = Field(default_factory=SlippageSettings)
    latency: LatencySettings = Field(default_factory=LatencySettings)
    optimizer: ExecutionOptimizerSettings = Field(default_factory=ExecutionOptimizerSettings)
    sizing: SizingSettings = Field(default_factory=SizingSettings)
    risk: ExecutionRiskSettings = Field(default_factory=ExecutionRiskSettings)
    experiments: StrategyExperimentSettings = Field(default_factory=StrategyExperimentSettings)
    falsification: FalsificationSettings = Field(default_factory=FalsificationSettings)

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
            raise ValueError(f"execution.mode debe ser 'paper', 'demo' o 'live', no {value!r}")
        return normalized

    def min_holding_seconds_for(self, strategy: str, category: str = "") -> float:
        """Minimum holding time before a regime change may close a position.

        Fallback en tres escalones: valor propio de la estrategia → valor de su
        categoría → valor global. Una estrategia nueva, o una posición sin
        atribución (adoptada del broker al arrancar), cae al global y se
        comporta exactamente como antes de este cambio.

        Args:
            strategy: Nombre de la estrategia dominante ("" si se desconoce).
            category: Categoría de esa estrategia ("" si se desconoce).

        Returns:
            Segundos mínimos de holding aplicables a esa posición.
        """
        by_strategy = self.regime_change_min_holding_by_strategy.get(strategy.strip().lower())
        if by_strategy is not None:
            return float(by_strategy)
        by_category = self.regime_change_min_holding_by_category.get(category.strip().lower())
        if by_category is not None:
            return float(by_category)
        return self.regime_change_min_holding_seconds

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


class ResearchBudgetSettings(BaseModel):
    """Presupuesto de CPU del ciclo autónomo del Research Lab (Fase 10).

    El laboratorio corre en la misma VPS que el motor que está operando (paper,
    pero con datos y notificaciones reales). Un ciclo de generación son cientos
    de backtests: sin un techo explícito puede robarle CPU al bucle de gestión
    de posiciones, que es el que no puede llegar tarde.

    Tres límites, los tres duros:

    1. **Ventana horaria** de baja actividad — fuera de ella el job no arranca.
    2. **Tope de trabajo por ejecución** — símbolos y genomas por lote.
    3. **Timeout duro** — el ciclo se cancela al vencer, pase lo que pase.

    Más dos vetos de cortesía: no arrancar con la CPU ya alta, ni con posiciones
    abiertas (el laboratorio puede esperar; una posición viva, no).
    """

    enabled: bool = True
    # Ventana UTC de baja actividad. Por defecto 01:00-05:00 UTC: fuera de la
    # sesión americana (cierra 22:00) y antes de que arranque Europa (07:00).
    # Si start > end la ventana cruza medianoche.
    window_start_hour_utc: int = 1
    window_end_hour_utc: int = 5
    # Techo de trabajo por ejecución. `max_generated_per_run` acota la población
    # del generador: es la variable que más multiplica el número de backtests.
    max_symbols_per_run: int = 2
    max_generated_per_run: int = 12
    # Timeout duro del ciclo completo. Al vencer se cancela: un ciclo colgado no
    # puede quedarse consumiendo CPU hasta el siguiente disparo.
    run_timeout_seconds: float = 900.0
    # Workers del cluster de simulación durante el ciclo autónomo. Se deja por
    # debajo de `simulation.max_workers` para no saturar una VPS de 2 vCPU.
    max_workers: int = 2
    # Vetos de cortesía.
    skip_if_cpu_pct_above: float = 70.0
    skip_if_positions_open: bool = True


class ResearchRollbackSettings(BaseModel):
    """Umbrales que **apagan** el ciclo autónomo si el motor operativo se degrada.

    El presupuesto decide si el ciclo puede *arrancar*; esto decide si hay que
    *apagarlo*. Son preguntas distintas: la primera mira el estado previo y su
    peor caso es posponer un ciclo; la segunda mira el efecto sobre el motor y
    su peor caso es haber estado degradando la operativa sin que nadie lo note.

    Sólo apaga el laboratorio. Nunca toca la operativa ni puede habilitar live:
    ante la duda, el que se sacrifica es el research.

    **No se rearma solo**: reactivar `auto_cycle` es una decisión humana. Un
    rollback reversible automáticamente convertiría un problema persistente en
    un ciclo de encendido/apagado, más difícil de diagnosticar que el fallo.
    """

    enabled: bool = True
    # CPU sostenida, no un pico: un pico aislado durante un ciclo de research es
    # exactamente lo esperado, y disparar con él haría la vigilancia inútil.
    max_cpu_pct: float = 85.0
    cpu_breaches_before_rollback: int = 3
    # El bucle de gestión de posiciones es el único que no puede llegar tarde.
    # Se compara contra su propia referencia, no contra un absoluto: lo que
    # importa es la degradación relativa, no el número de milisegundos.
    max_manage_latency_ratio: float = 2.0
    # Con muestra escasa no se juzga la latencia: comparar contra una línea base
    # que no existe es cómo se fabrican los falsos positivos.
    min_manage_passes: int = 30
    max_clock_skew_seconds: float = 5.0


class ResearchSettings(BaseModel):
    """Quant Research Lab (Fase 10): investiga, valida y promueve estrategias.

    Regla de oro: es un laboratorio **independiente de producción**. Nunca opera,
    nunca modifica estrategias en producción (trabaja sobre copias) y nunca
    habilita live trading. Descubre oportunidades y mejora el sistema con
    evidencia; la promoción final siempre exige aprobación humana.
    """

    enabled: bool = False
    # Punto 4 del Bloque 6 y punto 3 del Bloque 10: el ciclo queda cableado,
    # presupuestado y con rollback automático, pero **apagado**. Activarlo es
    # una decisión del operador, no del código. Hay un test que lo fija.
    # Ciclo autónomo de generación: sin esto el laboratorio existía pero **nada
    # lo disparaba** (experiments=0 indefinidamente), porque sólo se registraba
    # el notificador en el scheduler y nunca el laboratorio en sí.
    auto_cycle: bool = False
    cycle_interval_seconds: float = 86_400.0  # una vez al día
    cycle_symbols: list[str] = Field(
        default_factory=list,
        description="Símbolos del ciclo autónomo; vacío = los del Data Engine.",
    )
    cycle_timeframe: str = "1m"
    cycle_candles: int = 1_000  # historial por símbolo que alimenta el ciclo
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
    budget: ResearchBudgetSettings = Field(default_factory=ResearchBudgetSettings)
    rollback: ResearchRollbackSettings = Field(default_factory=ResearchRollbackSettings)


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
    portfolio: PortfolioIntelligenceSettings = Field(default_factory=PortfolioIntelligenceSettings)
    costs: CostAttributionSettings = Field(default_factory=CostAttributionSettings)
    shadow_benchmark: ShadowBenchmarkSettings = Field(default_factory=ShadowBenchmarkSettings)
    data_quality: DataQualitySettings = Field(default_factory=DataQualitySettings)
    meta_risk: MetaRiskSettings = Field(default_factory=MetaRiskSettings)
    backtesting: BacktestingSettings = Field(default_factory=BacktestingSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    watchdog: WatchdogSettings = Field(default_factory=WatchdogSettings)
    pipeline_watch: PipelineWatchSettings = Field(default_factory=PipelineWatchSettings)
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

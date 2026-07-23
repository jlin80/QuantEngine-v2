# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/);
versionado [SemVer](https://semver.org/lang/es/).

## [Unreleased]

## [0.10.0] — 2026-07-19 · Fase 10: Quant Research Lab y evolución autónoma

> **Regla absoluta:** el laboratorio **no opera**. Es independiente de producción,
> trabaja siempre sobre copias de las estrategias y **nunca** habilita live
> trading; la promoción final siempre exige aprobación humana. Live sigue
> deshabilitado (`allow_live=False`, `resolved_mode()→paper`). Repo sin commitear.

### Added
- **Quant Research Lab** (`app/research/`): laboratorio cuantitativo independiente
  que descubre, valida y promueve estrategias. Fachada `ResearchLab` con las
  funciones del spec (`create_experiment`, `generate_strategy`, `optimize_strategy`,
  `validate_candidate`, `promote_strategy`, `reject_strategy`, `rank_strategies`,
  `generate_feature`, `generate_report`, `archive_experiment`).
- **Strategy Generator** por reglas (`strategy_generator/`): combina 10 bloques de
  señal (EMA/SMA cross, Donchian/ATR breakout, momentum, RSI/VWAP reversion, MACD,
  delta momentum, CVD) y 4 filtros de contexto (sesión, volatilidad, liquidez,
  régimen) con coherencia de polaridad y afinidad; compila genomas a `DecisionSource`.
- **Feature Lab** y **Factor Lab**: 9 features candidatas y 13 factores en 7
  familias, validados/rankeados por coeficiente de información contra el retorno
  futuro (sin lookahead).
- **Optimización multiobjetivo** (`genetic_optimizer/`): escalarización ponderada +
  frente de Pareto sobre el genético de la Fase 6. **Bayesian Lab** (`bayesian_lab/`):
  TPE sin dependencias externas + historial comparable.
- **Simulation Cluster**: evaluación concurrente acotada de backtests.
- **Candidate Pipeline** (`validation_pipeline/`): Backtesting → Walk Forward →
  Monte Carlo → Validación ML → Benchmark → Risk Review sobre `BacktestLab`.
- **Ranking Engine**, **Experiment Manager**, **Knowledge Base** (append-only) y
  **Candidate Store** versionado.
- **Shadow Mode** (`shadow_mode/`): comparación estadística (Welch) de una
  challenger contra la vigente sobre los mismos datos, sin órdenes ni Decision
  Engine. **Paper Validation** y **Promotion Manager** fail-closed.
- **Report Generator** (`report_generator/`), **ResearchNotifier** (Discord) y
  documentación automática a Notion de experimentos y promociones.
- **API** `/api/research/*` (status, report, catalog, candidates, experiments,
  knowledge, ranking, generate, promote, reject, archive).
- Cableado en `bootstrap.py` (`_build_research`, gate `settings.research.enabled`)
  y en el ciclo de vida del engine (notificador). ~40 clases nuevas.

### Testing
- +48 pruebas (`tests/unit/test_research_*.py`): generador/compilador, labs,
  optimizadores, pipeline, ranking, experimentos, conocimiento, shadow, paper,
  promoción, fachada de extremo a extremo, rutas del dashboard y notificador.
  Suite total en verde; `ruff`+`black`+`mypy --strict` limpios en `app`.

## [0.9.0] — 2026-07-18 · Fase 9: Producción, DevOps y operación 24/7

> **Regla absoluta:** Live trading sigue **deshabilitado**. Toda la maquinaria de
> esta fase construye el camino a live, pero ninguna pieza lo abre: `allow_live`
> nace en `False`, `resolved_mode()` fuerza `paper` y el Live Gate no tiene
> atajos. Toda acción se audita; nunca se elimina una auditoría. Repo sin
> commitear (commits manuales del usuario).

### Added
- **Documentación automática a Notion** con cola de sincronización en disco
  (`NotionJournalBackend`): crea una página por entrada; si Notion cae, encola y
  el job `notion_sync` reintenta. Fachada `ProductionAPI.sync_notion()`.
- **Reportes operativos automáticos** (`app/production/reporting/`): resumen
  horario y reporte diario (PnL, capital, drawdown, win rate, PF, trades,
  CPU/RAM/latencia, estado de ML/brokers/Notion/Discord) al canal `reportes`;
  `send_daily_report()` / `send_hourly_report()`.
- **Backups del estado crítico** (`app/production/backup/`): `tar.gz` +
  manifiesto con SHA-256 verificado, rotación por retención, restauración segura
  (rechaza backups corruptos, anti path traversal); `backup_database()` /
  `restore_database()`.
- **Continuous Improvement Engine** (`app/production/improvement/`): detecta
  módulos grandes, bloques duplicados, TODOs, módulos sin pruebas y jobs
  inestables → lista priorizada, auditada y documentada en Notion.
- **Discord multicanal** (`RoutedDiscordChannel`): webhook por canal lógico
  (sistema/trading/errores/backtesting/ml/produccion/reportes) con ruteo por
  fuente/nivel; retrocompatible con un único webhook.
- **Seguridad** (`app/security/`): middleware con rate limiting token-bucket +
  cabeceras defensivas, validación de configuración de producción y vigilancia de
  rotación de secretos (nunca lee el valor del secreto).
- **Alta disponibilidad** (`app/production/failover/`): arriendo de líder
  primario/standby, fail-closed a standby.
- **Update / License / Maintenance managers** preparados: comprobación de
  versiones y migraciones sin auto-deploy, licencias sin restricciones (por
  diseño), ventana de mantenimiento y limpieza de temporales.
- **Endpoints**: `/api/{security/report, backups, backups/create, backups/restore,
  updates/check, maintenance/enter|exit, improvement/analyze, notion/sync,
  reports/send}`.
- **Observabilidad/DevOps**: dashboard Grafana provisionado
  (`docker/grafana/dashboards/quantengine.json`), reglas de alerta de Prometheus,
  CI endurecido (build de imágenes Docker sin push; despliegue = decisión humana).
- **123 tests nuevos** (593 en total) para Notion, Discord router, backups,
  reportes, mejora continua, updates, licencias, mantenimiento, failover,
  seguridad y los endpoints de operación.

### Changed
- `ProductionAPI` incorpora los subsistemas de operación y amplía `system_status`.
- `_build_production` cablea la capa completa; nuevos jobs del scheduler
  (reportes, backup, notion sync, mejora, mantenimiento, failover).
- `Notification` gana un campo opcional `channel` (ruteo lógico, retrocompatible).
- Configuración: nuevas secciones `production.{reporting,backup,updates,licenses,
  maintenance,failover,improvement}`, `security`, y `discord.{channels,routing}` /
  `notion.{version,queue_path,...}`.

## [0.8.0] — 2026-07-17 · Fase 8: Dashboard profesional, centro de control y observabilidad

> **Regla absoluta:** Live (Fase 9) sigue **deshabilitado**. Ninguna acción del
> dashboard puede habilitar live trading: el guard anti-live rechaza cualquier
> intento y `resolved_mode()` sigue forzando `paper`. Todas las escrituras se
> auditan. Repo sin commitear (commits manuales del usuario).

### Added
- **Frontend del dashboard** en `dashboard/` (Next.js 16 App Router + React 19 +
  TypeScript + TailwindCSS v4 + shadcn/ui + TanStack Query + Zustand + Framer
  Motion + TradingView Lightweight Charts). Tema oscuro por defecto, responsive.
- **14 pantallas**: Overview, Operations, Market, Strategies, Order Flow, Machine
  Learning, Backtesting, Trade Journal, Logs, Health Center, Reports, Alerts,
  Settings (Config Center) — todas consumiendo la API REST + WebSocket del motor.
- **Tiempo real** vía WebSocket `/ws/events`: cliente resiliente (backoff+jitter),
  store en vivo (precios/eventos/alertas) e invalidación de queries por evento.
- **Capa de comandos del backend** (`app/dashboard/api/`): CORS ampliado a
  escrituras; endpoints `/api/config` (GET/PATCH con whitelist), control de
  estrategias (`enable/disable/weight`), `/api/backtesting/run|cancel`,
  `/api/integrations/{discord,notion}` (webhook **enmascarado** + test),
  `/api/logs`, `/api/reports/{generate,list}`, `/api/audit`.
- **Guard anti-live** (`guard.py`), **audit log append-only** JSONL (`audit.py`,
  `GET /api/audit`) y **config store** de overrides persistidos (`config_store.py`).
- **Workspace System** (vistas guardadas: Trading/Monitoring/Order Flow/…),
  **command palette** global (⌘/Ctrl-K) y atajos de teclado.
- Reportes exportables (JSON/Markdown/CSV) con descarga cliente.

### Notes
- Backtests reales, walk-forward y Monte Carlo siguen ejecutándose vía
  `BacktestLab`/CLI (operación pesada); el endpoint acepta y audita, los
  resultados aparecen en Experiments.
- La aplicación en caliente de overrides a subsistemas ya en marcha se cablea de
  forma progresiva (se leen al arranque/recarga). Verificación con datos vivos
  pendiente en el ambiente `paper`.
- Calidad: frontend `tsc`+ESLint+Vitest (21 tests) en verde y `next build` OK;
  backend ruff + black + mypy strict en verde. ADR-058…ADR-062.

## [0.7.0] — 2026-07-17 · Fase 7: Machine Learning, IA y aprendizaje continuo

> **Regla absoluta:** el ML **asesora, no decide**. Ningún modelo abre
> operaciones por sí solo ni habilita live trading; toda recomendación pasa por
> el Decision Engine, el Risk Manager y el resto de validaciones. El sistema
> sigue operando **solo en paper trading**. Ninguna caja negra: toda predicción
> es explicable y trazable.

### Added
- **Capa de ML completa** (`app/ml/`, fachada `MLEngine`) con las APIs que pide
  la fase: `train_model`, `evaluate_model`, `predict_trade_quality`,
  `calculate_strategy_weight`, `detect_drift`, `rank_strategies`,
  `recommend_parameters`, `explain_prediction`, `register_model`,
  `activate_model`, `rollback_model`, `run_auto_ml` + orquestación asíncrona
  (`run_nightly_training`/`run_drift_check`/`run_meta_evaluation`).
- **Modelos intercambiables** tras una interfaz común: Regresión Logística,
  Árbol de Decisión, Random Forest y Extra Trees en **Python puro** (sin
  dependencias); XGBoost/LightGBM/CatBoost/redes preparados tras la misma
  interfaz (fallan con mensaje claro si su librería no está); **Ensemble**
  (voting/stacking).
- **Feature Store profesional** versionado (nombre/versión/descripción/fuente/
  tipo/validez/dependencias), never-overwrite y cómputo único con cache —
  distinto del Feature Store de mercado. Features = **contexto de la decisión**,
  nunca el precio; el resultado de la operación es la **etiqueta**.
- **Model Registry** versionado (never-overwrite, estados, autoría, dataset,
  métricas), activación reversible con **rollback inmediato**, historial de
  auditoría y persistencia JSON.
- **Entrenamiento honesto** (holdout temporal + walk-forward CV) y **puerta de
  validación** que **nunca activa un modelo inferior**; sólo autoactiva si
  `auto_activate` está encendido.
- **Predicción explicable** (contribución por variable + razones legibles;
  nunca sólo un número) y degradación con elegancia sin modelo activo.
- **Detección de deriva** (feature=PSI / concept=win-rate / performance / model):
  alerta, reduce la confianza y programa reentrenamiento — nunca para la
  operativa.
- **AutoML** (leaderboard por objetivo; salta backends no instalados sin caer),
  **AI Advisor** explicable, **RiskAdvisor** (estima, no opera) y **Strategy
  Intelligence** (ranking por evidencia reciente/histórica/segmentada).
- **Meta Strategy Manager** (mejora obligatoria): gobierna activación, prioridad
  y **peso** de las estrategias por configuración —nunca su código, nunca opera—;
  sube el peso de las consistentes, desactiva las degradadas tras N periodos y
  guarda auditoría.
- **Notificaciones ML por Discord** desacopladas por eventos (`MLNotifier`,
  reutiliza el `NotificationService` de la Fase 1) y endpoints
  `/api/ml/{status,report,models,ranking,features,meta,predict,train,drift/check,meta/evaluate}`.
- **Cableado**: `MLEngine` + `MLNotifier` en el composition root
  (`_build_ml`, se entrena con el Trade Journal de la Fase 5), el notificador en
  la lista de servicios y jobs del scheduler (`ml_nightly_training`,
  `ml_drift_check`, `ml_meta_evaluation`). Config `QE_ML__*` (deshabilitado y sin
  autoactivación por defecto).

### Fixed
- Deuda de calidad de la capa ML de sesiones previas: 1 error de mypy
  (`api.py recommend_parameters`) y 13 issues de ruff (longitud de línea,
  condiciones Yoda, `int(round())` redundante, `zip`→`pairwise`, `__slots__` sin
  ordenar, signo menos ambiguo). `app/ml` en verde con ruff + mypy strict.

### Notes
- **58 tests de ML** nuevos (feature store, registro+rollback, entrenamiento/
  validación, predicción/explicabilidad, deriva, AutoML, ranking, Meta Manager,
  notificador, fachada end-to-end, endpoints y cableado). Suite total: **470**.
- Infra preparada (no entrenada aún): online/incremental learning, GPU,
  entrenamiento paralelo y aprendizaje por refuerzo (bandit). Numeración: el
  ROADMAP la había etiquetado "Fase 8"; se unifica como **Fase 7** (ML) según la
  bitácora de cierre de la Fase 6.

## [0.6.0] — 2026-07-17 · Fase 6: Backtesting, optimización y validación

> **Regla de oro:** el laboratorio valida estrategias con evidencia
> estadística; nada de esto habilita live trading. Ninguna estrategia llega a
> paper sin superar el Strategy Qualification Pipeline.

### Added
- **Laboratorio cuantitativo completo** (`app/backtesting/`), fachada
  `BacktestLab`. Reutiliza el Execution Engine de la Fase 5 sobre datos
  históricos — no mantiene un segundo motor.
- **Motor de backtest** (`engine/` + `simulator/` + `market.py`): conduce el
  `ExecutionEngine` real contra una `MarketDataService` histórica en memoria,
  con un **reloj de replay** inyectable (`utc_now()` pasa a ser un seam) y un
  camino intrabar OHLC para disparar stops/objetivos dentro de la vela.
- **Dataset Manager** (`datasets/`): carga OHLCV desde CSV/JSON Lines con
  dedupe, y genera series sintéticas deterministas para pruebas y benchmarks.
- **Estadística cuantitativa** (`metrics/`): extiende el Performance Engine con
  SQN, MAR, Kelly, payoff, rachas máximas, drawdown medio, exposición, tiempo
  en mercado y estructura para alpha/beta.
- **Optimización de parámetros** (`optimizer/`): Grid y Random funcionales,
  **algoritmo genético** funcional (con memoización); bayesiano y Optuna como
  estructura preparada que falla con un mensaje claro si se invoca.
- **Walk Forward Analysis** (`walk_forward/`): ventanas rolling/expanding/
  anchored, optimización in-sample + validación out-of-sample, estabilidad y
  eficiencia por pliegue.
- **Monte Carlo** (`monte_carlo/`): remuestreo/permutación de operaciones →
  distribución de resultados, drawdown esperado/peor, capital requerido,
  **riesgo de ruina** e intervalos de confianza.
- **Benchmarks** (`benchmark/`): Buy & Hold, Random, EMA Cross y VWAP básico,
  con comparación estrategia-vs-benchmark.
- **Detector de sobreoptimización** (`validation/overfitting.py`): curve
  fitting, degradación IS→OOS, data snooping y parámetros en el borde, con
  índice de riesgo y advertencias por severidad.
- **Strategy Qualification Pipeline** (`validation/qualification.py`): batería
  automática (técnica → backtest → criterios → Monte Carlo → robustez →
  benchmark → walk-forward) que **aprueba o rechaza con motivos explícitos**.
  Umbrales configurables (`QE_BACKTEST__CRITERIA__*`).
- **Experiment Manager** (`experiments/`): registro append-only, nunca
  sobrescribe. **Versionado** (`versioning/`, `parameter_sets/`) de conjuntos
  de parámetros (`1.0`, `1.1`, ...) con rollback no destructivo.
- **Reportes** (`reports/`): JSON, Markdown y HTML autocontenido (sparkline
  SVG de equity); PDF como estructura preparada.
- **Replay Engine** (`replay/`): control pausar/reanudar/velocidad/avanzar/
  retroceder sobre una serie de velas.
- **Infra AutoML** (`automl/`) y **Feature Store** versionado (`feature_store/`)
  como estructura preparada — sin entrenar modelos (llega en la Fase 7).
- **Notificaciones Discord del laboratorio** (`notifications.py`) y
  **endpoints** de solo lectura `/api/backtesting/{status,criteria,experiments}`.
- **Configuración** `QE_BACKTEST__*` (criterios, optimizador, walk-forward,
  Monte Carlo); `BacktestLab` registrado en el composition root.
- **34 pruebas nuevas** (412 en total) cubriendo motor, datasets, reloj,
  métricas, optimizadores, ventanas, Monte Carlo, benchmarks, calificación,
  experimentos, versionado, reportes, replay y la fachada.

### Changed
- `app/utils/time.py`: `utc_now()` ahora lee de un proveedor de tiempo
  inyectable (por defecto el reloj de pared → comportamiento de producción
  idéntico), para que el backtest reutilice el motor sin reescribirlo.
- Documentación: ADR-042…ADR-049 y "Riesgos conocidos (Fase 6)" en
  `docs/architecture.md`; nueva `docs/backtesting.md`; entrada de bitácora.

## [0.5.0] — 2026-07-17 · Fase 5: Execution Engine y Paper Trading

> **Regla de oro:** solo paper trading. Ninguna orden llega a un broker real;
> `mode` distinto de `paper` se fuerza a `paper`.

### Added
- **Capa de ejecución completa** (`app/execution/`), desacoplada del
  Strategy/Decision Engine. Flujo: `DecisionGenerated` → Risk Manager →
  Execution Engine → Paper Engine → Position/Portfolio Manager → Trade
  Journal → eventos → Discord. Ninguna estrategia envía órdenes.
- **Paper Engine** (`paper_engine/`): simulador de ejecución de alta
  fidelidad — bid/ask reales, spread, slippage, latencia (con deriva de
  precio), comisiones, rechazos aleatorios, ejecución parcial y gaps. Nunca
  asume ejecución perfecta; los cierres nunca se rechazan ni se parcializan.
- **Motores de simulación independientes**: comisiones por nocional/unidad/
  fijo con overrides por símbolo y maker/taker (`commission/`); slippage
  dinámico por volatilidad, liquidez, tamaño, sesión y tipo de orden
  (`slippage/`); latencia red+broker+exchange+interna con jitter
  (`latency/`).
- **Risk Manager profesional** (`risk_manager/`): riesgo por operación,
  pérdidas diaria/semanal/mensual, pérdidas consecutivas, nº de posiciones,
  exposición total/por símbolo/por correlación, filtros de spread y
  liquidez, **kill switch** por drawdown y **circuit breaker** por pérdida
  rápida en ventana móvil.
- **Position sizing** configurable (`sizing/`): monto fijo, % del capital,
  ATR, riesgo fijo, riesgo dinámico (por confianza) y Kelly parcial, con
  tope de exposición por operación.
- **Position Manager** (`position_manager/`): posiciones abiertas/cerradas,
  PnL flotante, exposición, riesgo vivo, break-even, trailing stop por ATR
  y detección de salida (stop/objetivo/tiempo/régimen).
- **Portfolio Manager** (`portfolio_manager/`): contabilidad de margen —
  balance, equity, capital libre/usado, PnL realizado/flotante, drawdown y
  exposición, con seguimiento del equity pico.
- **Order Manager** (`order_manager/`) con ciclo de vida completo y
  estructura OCO preparada.
- **Trade Journal** (`journal/`): registro exhaustivo append-only (memoria +
  JSON Lines) de cada operación con precios, costes, R, ATR, régimen, score,
  confianza y razones de entrada/salida.
- **Performance Engine** (`performance/`): win rate, profit factor,
  expectativa, R:R, drawdown máximo, Sharpe, Sortino, Calmar, Ulcer Index,
  recovery factor, tiempo medio en mercado y actividad por día/sesión.
- **Notificaciones de ejecución por Discord** (`notifications/`): servicio
  desacoplado que traduce cada evento (posición abierta/cerrada, stop
  movido, break-even, trailing, rechazo, riesgo, kill switch, circuit
  breaker) a embeds ricos; reportes periódicos (horario/diario).
- **Endpoints del dashboard**: `/api/execution/{status,portfolio,positions,
  trades,performance,risk,report,notifications}`.
- **Configuración** `QE_EXECUTION__*` (comisiones, slippage, latencia,
  sizing y riesgo) y sección `ExecutionSettings`; wiring en el composition
  root y en el ciclo de vida del `QuantEngine`.
- **Eventos**: `OrderCreated`, `OrderRejected`, `OrderExecuted`,
  `PositionOpened`, `PositionClosed`, `StopMoved`, `BreakEvenActivated`,
  `TrailingUpdated`, `RiskTriggered`, `KillSwitchTriggered`,
  `CircuitBreakerTriggered`, `DiscordNotificationSent/Failed`.
- **43 pruebas nuevas** (378 en total) cubriendo simulación, sizing, paper,
  managers, riesgo, performance, journal, notificaciones y el flujo
  extremo a extremo.

## [0.4.0] — 2026-07-17 · Fase 4: Biblioteca de estrategias

### Added
- **Biblioteca de 20 estrategias** como plugins (`app/strategies/`), todas
  desacopladas y sin capacidad de operar: VWAP Mean Reversion, VWAP
  Breakout, Anchored VWAP, Liquidity Sweep, Order Block, Fair Value Gap,
  BOS, CHOCH, MSS, Delta Confirmation, CVD, Order Book Imbalance, Volume
  Profile, Opening Range Breakout, Momentum Continuation, ATR Expansion,
  Volatility Compression, Trend Pullback, Mean Reversion y Range Breakout.
- **Base `QuantStrategy`** (`strategies/base/`): pipeline común
  pre-chequeos → `evaluate()` → confirmaciones → score de 5 componentes
  (calidad/fortaleza/contexto/probabilidad/riesgo, pesos configurables) →
  confianza multi-factor → señal estructurada con Entry/SL/TP, razones,
  advertencias y confirmaciones faltantes. Cero constantes en el código:
  todo parámetro es configurable por capas (base → subclase → env
  `QE_QUANT__STRATEGIES__<nombre>__…`) y optimizable en fases futuras.
- **Indicadores puros** (`app/analytics/indicators/`), deterministas y
  cacheados vía Feature Store:
  - SMC: FVG (con % de relleno), order blocks (mitigated/breaker), sweeps,
    equal highs/lows, premium/discount, BOS/CHOCH/MSS.
  - Market structure: swing points, HH/HL/LH/LL, breakouts válidos/falsos,
    consolidación, acumulación/distribución.
  - Order flow: bid/ask volume, delta, CVD, agresores, book pressure,
    imbalance, absorción, exhaustión, consumo de liquidez, spoofing e
    iceberg experimentales.
  - VWAP: sesión (día/semana/mes), anchored, bandas σ, pendiente,
    distancia %.
  - Volume profile: POC, VAH/VAL, HVN/LVN, perfil por ventana
    configurable (sesión o compuesto).
  - ATR: clásico, adaptativo, slope, expansión/compresión.
  - Momentum: ROC, momentum score, aceleración, impulsos.
  - Liquidez: pools, stop hunts, grabs, sweep confirmation.
- **Motor de confirmaciones** (`strategies/confirmation/`): delta, CVD,
  volumen, spread, volatilidad, sesión, régimen, libro y volume profile;
  las fallidas marcan la señal (`required_confirmation`) con advertencia
  explícita, nunca se descartan en silencio.
- **Framework de Evaluación Continua** (`app/engine/evaluation/`):
  operaciones virtuales por señal resueltas contra velas (TP/SL/timeout en
  R) → win rate, profit factor, expectativa, drawdown, falsas señales y
  tiempo medio por estrategia; snapshot JSON periódico y hook `factor()`
  para el ajuste dinámico de pesos futuro. NO se usa para operar.
- **APIs internas estables** (`strategies/shared/api.py`):
  `detect_liquidity/fvg/order_block`, `calculate_vwap/delta/cvd/
  volume_profile/market_structure/momentum/atr/regime_score`,
  `generate_strategy_signal`.
- **Eventos de detección**: LiquidityDetected, FVGDetected,
  OrderBlockDetected, VWAPCalculated, DeltaCalculated, CVDCalculated,
  VolumeProfileUpdated, MomentumDetected, StrategyScoreUpdated — emitidos
  por el Strategy Engine desde `ctx.detections`.
- **Dashboard**: `/api/engine/strategies/{name}` con estado, última
  ejecución, tiempo de cálculo, score, confianza, razones y rendimiento
  virtual acumulado.
- **Tests**: ~100 nuevos (unitarios de cada familia de indicadores +
  integración de la biblioteca completa + evaluación continua);
  determinismo y consistencia verificados.

### Docs
- `docs/strategies.md`: catálogo de la biblioteca (supuestos, parámetros,
  confirmaciones, regímenes preferidos y limitaciones por estrategia).
- ADR-029…ADR-033 en `docs/architecture.md`; ROADMAP renumerado (la
  ejecución pasa a Fase 5).

## [0.3.0] — 2026-07-16 · Fase 3: Quant Core + Strategy Engine

### Added
- **Framework de estrategias** (`BaseStrategy`): las estrategias analizan y
  devuelven una `StrategySignal` estructurada (dirección, confianza, score
  0-100, zona de entrada, SL/TP, RR, razones, advertencias, expiración,
  contexto) — nunca compran ni venden; `analyze/validate/score/explain`.
- **Sistema de plugins**: descubrimiento automático de estrategias en
  `app/strategies/` (`PluginLoader`); un plugin roto jamás bloquea el resto;
  carga/descarga/enable/disable en caliente.
- **Strategy Engine**: cadencia por estrategia (tick, segundo, vela cerrada,
  intervalo) sin bloqueo mutuo; lock por estrategia con conteo de disparos
  saltados; aislamiento de errores (`StrategyFailed`); métricas de tiempo de
  ejecución por estrategia (última/EMA) para detectar cuellos de botella.
- **Signal Engine**: validación estructural (`SignalValidator`: coherencia
  SL/TP/entrada, rangos, expiración), deduplicación con supersede, orden por
  prioridad (score × confianza), expiración por TTL, detección de conflictos.
- **Decision Engine** (único autorizado a decidir): consenso + confianza +
  contexto + filtros ⇒ UNA `Decision` explicable; nunca "no operar" a secas —
  toda causa (umbral o filtro) aparece en la explicación.
- **Motor de consenso configurable**: majority voting, weighted voting,
  weighted average (con signo), dynamic weighting (rendimiento reciente) y
  regime weighting (multiplicadores por régimen); pesos por estrategia
  configurables y modificables en caliente.
- **Confidence Engine**: confianza 0-1 independiente del score, con desglose
  (calidad de datos, liquidez, volatilidad, confirmaciones, acuerdo,
  historial); score alto + confianza baja = no operar.
- **Market Context Engine**: régimen, sesiones (asia/europa/américa, con
  solape y cruce de medianoche), volatilidad por ATR%, spread elevado,
  volumen suficiente, ventanas de noticias, calidad del dato (0-1).
- **Regime Detection**: trending/ranging/expansion/compression/breakout/
  reversal/high-low volatility con métricas de soporte (efficiency ratio,
  ratio ATR corto/largo, ruptura de rango).
- **Feature Store** central con cálculo único por ventana (TTL) y métricas
  hits/misses: ATR, VWAP, EMA, volumen, spread, delta, CVD, book pressure,
  imbalance, open interest, funding.
- **Sistema de filtros** independientes con veto y razón obligatoria:
  sesión, spread, volatilidad, liquidez, noticias, drawdown, correlación;
  la cadena evalúa TODOS (explicabilidad, sin cortocircuito).
- **Historial completo** (`SignalHistoryStore` + `HistoryWriter`): toda señal
  (aceptada/rechazada/expirada/superseded) y toda decisión, en memoria y
  persistidas por lotes con spill a JSONL (tablas `strategy_signals` y
  `engine_decisions`, migración Alembic 0002); factor de rendimiento
  reciente por estrategia.
- **QuantCore** (fachada de APIs internas): load/unload/enable/disable
  strategy, analyze_market, detect_regime, build_consensus, calculate_score,
  calculate_confidence, evaluate_filters, generate_signal, decide, status.
- **Eventos nuevos**: SignalRejected, SignalExpired, ConsensusReached,
  DecisionGenerated, FilterTriggered, MarketRegimeChanged, ContextUpdated,
  StrategyExecuted, StrategyFailed, StrategyUnloaded (+ StrategyLoaded y
  SignalCreated de Fase 1).
- **Endpoints del dashboard**: `/api/engine/status`, `/strategies`,
  `/signals`, `/decisions`, `/regime/{s}`, `/context/{s}`, `/filters/{s}`,
  `/consensus`.
- **Configuración** `QE_QUANT__*`: consenso (método/umbrales), confianza
  (pesos), contexto (sesiones/umbrales/noticias), régimen, filtros,
  historial y parámetros por estrategia.
- 97 pruebas nuevas (233 en total): plugins, ejecución concurrente y
  aislamiento, cadencias, scoring, los 5 algoritmos de consenso, confianza,
  filtros, detección de régimen y conflictos, persistencia de señales,
  explicabilidad de decisiones y endpoints.

### Fixed
- Import circular dashboard ⇄ engine (el paquete `app.engine` ya no
  re-exporta el bootstrap).
- Las señales resueltas ahora sí se encolan a persistencia (sink
  `SignalHistoryStore → HistoryWriter`; antes solo se persistían decisiones).

## [0.2.0] — 2026-07-16 · Fase 2: Data Engine

### Added
- **Data Engine completo** (`app/market/`): única fuente de verdad de datos
  de mercado; ningún módulo se conecta a un broker directamente.
- **Modelos internos tipados e inmutables**: Ticker, Trade, OHLCV, Candle,
  DepthLevel, OrderBook (con spread/mid/microprice/imbalance/liquidez/book
  pressure), OrderBookDelta, FundingRate, OpenInterest, Liquidation,
  MarketSnapshot, MarketState; doble timestamp UTC + latencia en todo.
- **Timeframes**: tick, 1s–30s, 1m–30m, 1h, 4h, 1d, 1w, 1M con aritmética de
  buckets (semana ISO y mes calendario incluidos).
- **Proveedores**: Binance, Bybit y OKX funcionales (WS + REST de
  reconstrucción); Bitget, OANDA, MetaTrader 5 e Interactive Brokers
  preparados tras la misma interfaz. `ProviderRegistry` para añadir brokers
  sin tocar el núcleo.
- **Normalizadores por exchange** — todos los formatos mueren en la frontera
  del proveedor y salen como el mismo objeto interno.
- **WebSocket manager profesional**: reconexión automática con backoff
  exponencial + jitter, heartbeat ping/pong, compresión permessage-deflate,
  detección de conexión muda, resuscripción tras reconectar, métricas y
  latencia por conexión; transporte inyectable (tests sin red).
- **TickCollector**: pipeline central con cola no bloqueante — validar →
  estado → velas → libro → cache → storage → eventos.
- **DataValidator**: descarta precios/paños corruptos, duplicados,
  fuera-de-orden, timestamps futuros, saltos imposibles y volúmenes absurdos;
  publica `DataQualityAlert`.
- **CandleAggregator**: trades → velas de cualquier timeframe (OHLCV, VWAP,
  volumen comprador/vendedor, conteo de trades), cierre por rollover y por
  expiración (`flush_stale`).
- **OrderBookManager**: reconstrucción por snapshot + deltas incrementales
  con detección de huecos de secuencia y resync automático vía REST.
- **MarketStateStore + MarketCache**: estado vivo en memoria + segunda copia
  en Redis (`mkt:*`) con degradación a memoria.
- **Persistencia**: tablas `market_ticks` y `market_candles` (migración
  Alembic 0001), `MarketDataWriter` batched con spill a JSONL si la DB cae,
  `MarketDataRepository` de lectura.
- **MarketDataService** (API interna agnóstica del exchange):
  `subscribe/unsubscribe`, `get_last_price`, `get_ticker`, `get_orderbook`,
  `get_latest_candle`, `get_candles`, `get_recent_trades`, `get_spread`,
  `get_depth`, `get_funding_rate`, `get_open_interest`,
  `get_market_snapshot`, `get_market_state`, `get_tick_stream`.
- **Eventos de mercado**: NewTick, TradeReceived, TickerUpdated,
  OrderBookUpdated, OrderBookResyncRequired, CandleClosed, FundingUpdated,
  OpenInterestUpdated, LiquidationReceived, DataQualityAlert,
  ConnectionRecovered, FeedSubscribed/Unsubscribed (+ PriceUpdated,
  ConnectionLost/Restored de Fase 1).
- **Jobs del scheduler**: cierre de velas expiradas, vigilancia de
  conexiones, drift de reloj contra cada exchange, snapshot de métricas.
- **Endpoints del dashboard**: `/api/market/status`, `/symbols`,
  `/price/{s}`, `/ticker/{s}`, `/orderbook/{s}`, `/candles/{s}`,
  `/trades/{s}`, `/snapshot/{s}`.
- **Métricas**: ticks/s, mensajes WS/s, latencia de datos (EMA/min/max),
  latencia de ping, reconexiones, errores, rechazados, descartados.
- 94 pruebas nuevas (136 en total): reconexión, resuscripción,
  normalización por exchange, validación, agregación, order book,
  timestamps, duplicados, latencia, writer con spill, servicio y API.

### Changed
- `Event.to_dict()` ahora es JSON-safe recursivo (datetimes/enums anidados).
- ADR-013…ADR-019 en `docs/architecture.md` (+ riesgos y próximas tareas).
- Dependencia nueva: `websockets`.

## [0.1.0] — 2026-07-16 · Fase 1: Infraestructura

### Added
- Estructura modular completa del proyecto (Clean Architecture, SOLID, DI).
- **Event Bus** asyncio: pub/sub tipado, comodín, aislamiento de errores por
  handler, dead letters, métricas.
- **Sistema de configuración** centralizado (pydantic-settings): secciones
  Trading/Broker/Database/Discord/Notion/Dashboard/ML/Risk/Logging/Paper/
  Backtesting/Health/Watchdog; 4 ambientes (development/testing/paper/production).
- **Logging profesional**: rotación, archivo por módulo, `errors.log`,
  formato JSON opcional, buffer de errores recientes para el Health Monitor.
- **Excepciones personalizadas** con contexto y clasificación de recuperabilidad.
- **NotificationService** desacoplado + canal **Discord Webhook** (único canal
  de esta versión) con manejo de rate-limit y reintentos.
- **CacheService**: Redis primario con degradación automática a memoria y
  reintento tras cooldown.
- **Capa de base de datos** preparada: SQLAlchemy async, pool, sesión
  transaccional, Alembic async (sin tablas todavía).
- **AsyncScheduler** para tareas periódicas con aislamiento de errores.
- **HealthMonitor**: CPU/RAM/disco/uptime/lag del event loop/estado de módulos/
  errores recientes; publica cambios de estado como eventos.
- **Watchdog**: heartbeats, detección de módulos congelados, reinicio
  automático con presupuesto, detección de errores repetitivos.
- **API FastAPI**: `/api/health`, `/api/system/status`, `/api/system/info`,
  WebSocket `/ws/events`; servida por uvicorn dentro del motor.
- **DocumentationService** (bitácora): backend Markdown activo, Notion preparado.
- **Docker Compose**: backend, PostgreSQL, Redis; perfiles para dashboard,
  TimescaleDB, Prometheus y Grafana.
- **CI (GitHub Actions)**: ruff + black + mypy + pytest con cobertura.
- Suite de pruebas unitarias e integración (event bus, config, cache,
  notificaciones, scheduler, watchdog, health, documentación, API, y ciclo de
  vida completo del motor con su composition root).

[0.3.0]: https://example.invalid/quant-engine/releases/0.3.0
[0.2.0]: https://example.invalid/quant-engine/releases/0.2.0
[0.1.0]: https://example.invalid/quant-engine/releases/0.1.0

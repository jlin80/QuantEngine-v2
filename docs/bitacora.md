# Bitácora del proyecto — Quant Engine V2

## 2026-07-17 00:04 UTC — Fase 1 completada — infraestructura base

**Categoría:** milestone · **Tags:** `fase-1` `arquitectura` `infraestructura`

Se completó la Fase 1 del proyecto: base arquitectónica sin funcionalidad de trading.

**Entregado:**
- Estructura modular completa (Clean Architecture, SOLID, DI en `app/engine/bootstrap.py`).
- Event Bus asyncio con aislamiento de errores, dead letters y métricas.
- Configuración centralizada tipada con 4 ambientes (development/testing/paper/production).
- Logging con rotación, archivo por módulo, JSON opcional y buffer de errores recientes.
- Jerarquía de excepciones con contexto y clasificación de recuperabilidad.
- NotificationService + canal Discord Webhook (único canal por regla global).
- CacheService con Redis primario y degradación automática a memoria.
- Capa de datos preparada (SQLAlchemy async + Alembic, sin tablas).
- Scheduler interno, Health Monitor y Watchdog con reinicio automático.
- API FastAPI (REST + WebSocket de eventos) embebida en el motor.
- Docker Compose (backend/PostgreSQL/Redis + perfiles timescale/monitoring/dashboard).
- CI GitHub Actions (ruff, black, mypy, pytest+cov) y suite de tests.

**Decisiones técnicas:** documentadas como ADR-001…ADR-012 en `docs/architecture.md`.

**Pendiente para Fase 2:** feeds de datos de mercado, primeras tablas y migraciones.

## 2026-07-17 01:56 UTC — Fase 2 completada — Data Engine

**Categoría:** milestone · **Tags:** `fase-2` `data-engine` `mercado`

Se completó la Fase 2 del proyecto: Data Engine profesional, única fuente de
verdad de datos de mercado. Ningún módulo se conecta a un broker directamente.

**Entregado:**
- Proveedores Binance, Bybit y OKX funcionales (WS + REST de reconstrucción);
  Bitget, OANDA, MetaTrader 5 e IBKR preparados tras la misma interfaz
  (`ProviderRegistry`: añadir un broker no toca el núcleo).
- Normalizadores por exchange → modelo interno único, tipado e inmutable
  (Ticker/Trade/OHLCV/Candle/OrderBook/DepthLevel/Funding/OpenInterest/
  Liquidation/MarketSnapshot/MarketState) con doble timestamp UTC + latencia.
- WebSocket manager: reconexión con backoff exponencial + jitter, heartbeat
  ping/pong, compresión, detección de conexión muda, resuscripción automática,
  métricas y latencia por conexión; transporte inyectable (tests sin red).
- Pipeline `TickCollector`: validación de calidad (duplicados, fuera de orden,
  gaps imposibles, timestamps inválidos, volumen absurdo) → estado vivo →
  agregador de velas (cualquier timeframe: tick a mensual, VWAP, volumen
  comprador/vendedor) → order book incremental con resync REST → cache Redis
  degradable → persistencia batched con spill a disco → eventos.
- Primeras tablas: `market_ticks` y `market_candles` (migración Alembic 0001).
- `MarketDataService`: API interna agnóstica del exchange (get_last_price,
  get_orderbook, get_latest_candle, get_tick_stream, get_market_snapshot,
  get_spread, get_depth, get_recent_trades, get_open_interest,
  get_funding_rate, subscribe/unsubscribe).
- Endpoints `/api/market/*`, jobs del scheduler (flush de velas, vigilancia de
  conexiones, drift de reloj, métricas) y eventos nuevos del bus.
- 136 tests (94 nuevos); ruff + black + mypy strict en verde.
- Smoke test real: stream público de Binance con BTCUSDT/ETHUSDT en vivo
  (~500 msg/s, ~3.200 ticks en 20 s) servido por la API.

**Decisiones técnicas:** ADR-013…ADR-019 en `docs/architecture.md`
(incluye riesgos conocidos y próximas tareas).

**Pendiente para Fase 3:** Quant Core + Strategy Engine (consenso
multi-estrategia), backfill histórico, MT5/OANDA para XAUUSD real,
reconciliación automática de spills.

## 2026-07-17 04:01 UTC — Fase 3 completada — Quant Core + Strategy Engine (v0.3.0)

**Categoría:** milestone · **Tags:** `fase-3` `quant-core` `strategy-engine` `decision-engine`

Se completó la Fase 3 (v0.3.0): el "cerebro" del sistema — capaz de recibir
estrategias, evaluarlas y producir una decisión estructurada, sin tocar el
mercado.

**Entregado:**
- Framework de estrategias (`BaseStrategy`): analizan y devuelven `StrategySignal` estructurada; nunca abren operaciones. Plugins con descubrimiento automático en `app/strategies/` (un plugin roto jamás bloquea el resto).
- Strategy Engine: cadencia por estrategia (tick/segundo/vela/intervalo) sin bloqueo mutuo, aislamiento de errores y métricas de tiempo por estrategia.
- Signal Engine (validación, dedupe/supersede, prioridad, TTL, conflictos) y Decision Engine único: consenso (5 algoritmos intercambiables) + confianza separada del score + contexto/régimen + filtros con veto ⇒ UNA decisión SIEMPRE explicable.
- Feature Store central (cálculo único por ventana), Market Context Engine, Regime Detection, filtros (sesión/spread/volatilidad/liquidez/noticias/drawdown/correlación).
- Historial completo sin borrado + persistencia batched con spill (`strategy_signals`/`engine_decisions`, migración 0002) y factor de rendimiento por estrategia.
- QuantCore (APIs internas), endpoints `/api/engine/*`, eventos nuevos del núcleo.

**Correcciones de esta sesión:** import circular dashboard⇄engine (paquete `app.engine` ya no re-exporta bootstrap); las señales resueltas ahora sí se persisten (sink historial→writer); limpieza ruff (pairwise, ClassVar, noqa).

**Calidad:** ruff+black+mypy strict (199 archivos) en verde; 233 tests (97 nuevos de Fase 3: plugins, concurrencia, consenso, filtros, régimen, persistencia, explicabilidad, API); cobertura 83 %.

**Decisiones técnicas:** ADR-020…ADR-028 en `docs/architecture.md` (+ riesgos Fase 3 y próximas tareas).

**Regla respetada:** sin estrategias concretas, sin ejecución de órdenes, sin paper trading — eso abre la siguiente fase.

## 2026-07-17 16:00 UTC — Fase 4 completada — Biblioteca de estrategias (v0.4.0)

**Categoría:** milestone · **Tags:** `fase-4` `estrategias` `smc` `orderflow` `vwap` `evaluacion-continua`

Se completó la Fase 4 (v0.4.0): la biblioteca profesional de estrategias.
El sistema analiza el mercado con 20 estrategias desacopladas que puntúan,
explican y proponen niveles — y sigue sin poder tocar un broker.

**Entregado:**
- 20 estrategias como plugins (`app/strategies/`): VWAP mean reversion/breakout/anchored, Liquidity Sweep, Order Block, FVG, BOS, CHOCH, MSS, Delta, CVD, Order Book Imbalance, Volume Profile, ORB, Momentum Continuation, ATR Expansion, Volatility Compression, Trend Pullback, Mean Reversion, Range Breakout. Catálogo completo (supuestos/parámetros/limitaciones) en `docs/strategies.md`.
- Base `QuantStrategy`: pre-chequeos → `evaluate()` → confirmaciones → score de 5 componentes → confianza multi-factor → señal estructurada. Cero constantes: todo parámetro por configuración, listo para el optimizador.
- Indicadores puros y deterministas (`app/analytics/indicators/`): SMC completo, market structure, order flow (con spoofing/iceberg experimentales), VWAP + bandas, volume profile, ATR avanzado, momentum, liquidity engine — todos cacheados vía Feature Store (cálculo único por ventana).
- Motor de confirmaciones bajo demanda (10 tipos); las fallidas marcan la señal, jamás se ocultan.
- Framework de Evaluación Continua: operaciones virtuales por señal → win rate, profit factor, expectativa (R), drawdown, falsas señales, tiempo medio; snapshot JSON + hook `factor()` para ajuste dinámico de pesos futuro. NO se usa para operar.
- APIs internas estables (`detect_*`/`calculate_*`), 9 eventos de detección nuevos, endpoint de detalle por estrategia.

**Calidad:** ruff + black + mypy strict en verde; 335 tests (102 nuevos: indicadores, base, señales, biblioteca completa, evaluación); cobertura 83 %.

**Decisiones técnicas:** ADR-029…ADR-033 en `docs/architecture.md` (+ riesgos Fase 4). ROADMAP renumerado: la ejecución pasa a ser Fase 5.

**Regla respetada:** sin ejecución de órdenes, sin Position/Risk Manager, sin paper trading, sin ML.

## 2026-07-17 18:00 UTC — Fase 5 completada — Execution Engine + Paper Trading (v0.5.0)

**Categoría:** milestone · **Tags:** `fase-5` `execution` `paper-trading` `risk-manager` `portfolio` `discord`

Se completó la Fase 5 (v0.5.0): el motor de ejecución profesional,
desacoplado del Strategy/Decision Engine. **Regla de oro respetada: solo
Paper Trading** — ninguna orden llega a un broker real (`mode` distinto de
`paper` se fuerza a `paper`).

**Entregado:**
- Capa completa `app/execution/` (14 módulos de responsabilidad única):
  models, events, commission, slippage, latency, sizing, paper_engine,
  position_manager, portfolio_manager, order_manager, risk_manager,
  journal, performance, notifications.
- Flujo único dirigido por eventos: `DecisionGenerated` → Risk Manager →
  Execution Engine → Paper Engine → Position/Portfolio Manager → Trade
  Journal → eventos → Discord. Ninguna estrategia envía órdenes.
- Paper Engine de alta fidelidad: bid/ask reales, spread, slippage
  adverso, latencia con deriva de precio, comisiones, rechazos aleatorios,
  ejecución parcial y gaps — nunca ejecución perfecta; los cierres nunca se
  rechazan ni se parcializan.
- Risk Manager: riesgo por operación, pérdidas diaria/semanal/mensual,
  pérdidas consecutivas, nº de posiciones, exposición total/símbolo/
  correlación, filtros de spread y liquidez, kill switch por drawdown y
  circuit breaker por pérdida rápida en ventana móvil.
- Position sizing configurable: monto fijo, % del capital, ATR, riesgo
  fijo, riesgo dinámico (por confianza) y Kelly parcial, con tope de
  exposición por operación.
- Position Manager (PnL flotante, exposición, break-even, trailing por
  ATR, detección de salida) y Portfolio Manager (balance, equity, capital
  libre/usado, drawdown contra equity pico).
- Order Manager con ciclo de vida completo y estructura OCO preparada.
- Trade Journal append-only (memoria + JSONL) y Performance Engine (win
  rate, profit factor, expectativa, R:R, drawdown, Sharpe, Sortino, Calmar,
  Ulcer Index, recovery factor, tiempo medio en mercado, actividad por
  día/sesión).
- Notificaciones Discord de ejecución (`ExecutionNotifier`) vía suscripción
  al bus, desacoplado del motor; reportes periódicos horario/diario.
- Config `QE_EXECUTION__*`, 12 eventos nuevos, endpoints
  `/api/execution/{status,portfolio,positions,trades,performance,risk,
  report,notifications}`.
- Documentación dedicada `docs/execution.md`.

**Calidad:** ruff + black en verde; mypy strict en verde salvo 3 errores
preexistentes en `tests/unit/test_engine_evaluation.py` (Fase 4, fuera de
alcance, archivo no tocado); 378 tests (43 nuevos: simulación, sizing,
paper, managers, riesgo, performance, journal, notificaciones, flujo
extremo a extremo).

**Decisiones técnicas:** ADR-034…ADR-041 en `docs/architecture.md` (+
riesgos Fase 5). ROADMAP: ejecución real (MT5/OANDA) pasa a Fase 6+, tras
definir criterios estadísticos de go-live.

**Regla respetada:** solo Paper Trading — ninguna orden se envió ni se
enviará a un broker real hasta cumplir criterios estadísticos definidos;
repo sigue sin commitear (commits manuales por regla del usuario).

## 2026-07-17 20:00 UTC — Fase 6 completada — Backtesting, optimización y validación (v0.6.0)

**Categoría:** milestone · **Tags:** `fase-6` `backtesting` `optimizacion` `walk-forward` `monte-carlo` `validacion` `overfitting`

Se completó la Fase 6 (v0.6.0): el laboratorio cuantitativo para validar
estrategias con evidencia estadística antes de paper trading. **Regla de oro
respetada: nada de esto habilita live trading.**

**Entregado:**
- Laboratorio completo `app/backtesting/` con fachada `BacktestLab`.
- Motor de backtest que **reutiliza el Execution Engine de la Fase 5** sobre
  datos históricos (no hay un segundo motor): `MarketDataService` histórica en
  memoria + reloj de replay inyectable (`utc_now()` pasa a ser un seam) +
  camino intrabar OHLC para stops/objetivos dentro de la vela.
- Dataset Manager (CSV/JSONL + sintéticos deterministas), estadística
  extendida (SQN, MAR, Kelly, payoff, rachas, drawdown medio, exposición),
  optimización (grid/random/genético funcionales; bayesiano/Optuna preparados),
  walk-forward (rolling/expanding/anchored, IS→OOS, estabilidad/eficiencia),
  Monte Carlo (distribución, drawdown, riesgo de ruina, IC), benchmarks
  (buy&hold/random/EMA/VWAP) y detector de sobreoptimización.
- **Strategy Qualification Pipeline** (mejora recomendada): batería automática
  que aprueba/rechaza una estrategia con motivos explícitos; umbrales
  configurables. Experimentos append-only + versionado de parámetros con
  rollback. Reportes JSON/MD/HTML (PDF preparado). Replay Engine. Infra AutoML
  y Feature Store versionado como estructura preparada (sin entrenar).
- Config `QE_BACKTEST__*`, `BacktestLab` en el composition root, endpoints
  `/api/backtesting/{status,criteria,experiments}` (solo lectura) y notificador
  Discord del laboratorio.

**Calidad:** ruff + black en verde; mypy strict en verde salvo los 3 errores
preexistentes de Fase 4 (fuera de alcance, archivo no tocado); 412 tests (34
nuevos). Verificado end-to-end: el motor reproduce el paper trading con costes
reales (spread/comisiones) y las salidas por stop/trailing se disparan intravela.

**Decisiones técnicas:** ADR-042…ADR-049 en `docs/architecture.md` (+ riesgos
Fase 6). Nueva `docs/backtesting.md`. ROADMAP: ML/IA pasa a Fase 7.

**Regla respetada:** ninguna estrategia se promueve sin pasar el laboratorio;
sigue sin habilitarse live trading; repo sin commitear (commits manuales).

## 2026-07-17 22:00 UTC — Fase 7 completada — Machine Learning, IA y aprendizaje continuo (v0.7.0)

**Categoría:** milestone · **Tags:** `fase-7` `ml` `ia` `feature-store` `model-registry` `drift` `meta-strategy` `discord`

Se completó la Fase 7 (v0.7.0): la capa de Machine Learning e IA interna.
**Regla absoluta respetada: el ML asesora, no decide.** Ningún modelo abre
operaciones ni habilita live trading; toda recomendación pasa por el Decision
Engine y el Risk Manager. Sigue en paper trading. Ninguna caja negra: toda
predicción es explicable.

Gran parte de la capa `app/ml/` (~5340 líneas) ya venía construida de sesiones
previas; esta sesión la **verificó, testeó, cableó y documentó**.

**Entregado:**
- Fachada `MLEngine` con las APIs de la fase (train/evaluate/predict/
  calculate_strategy_weight/detect_drift/rank/recommend/explain/register/
  activate/rollback/run_auto_ml) + orquestación async (nocturno/deriva/meta).
- Modelos intercambiables en **Python puro** (logística, árbol, random forest,
  extra trees) + backends pesados y ensembles (voting/stacking) preparados tras
  la misma interfaz. Features = contexto de la decisión, nunca el precio.
- Feature Store profesional versionado (never-overwrite + cómputo único), Model
  Registry versionado con **rollback inmediato** y auditoría, entrenamiento
  honesto (holdout + walk-forward) con **puerta de validación que nunca activa
  un modelo inferior**, predicción explicable, detección de deriva (feature/
  concept/performance/model) que reduce confianza y programa reentrenamiento,
  AutoML, AI Advisor, RiskAdvisor y Strategy Intelligence.
- **Meta Strategy Manager** (mejora obligatoria): gobierna activación/prioridad/
  peso por configuración —nunca el código, nunca opera—; boost del consistente,
  disable tras N degradaciones, auditoría.
- **Notificador ML por Discord** desacoplado por eventos (`MLNotifier`) y
  endpoints `/api/ml/*` (status/report/models/ranking/features/meta/predict/
  train/drift/meta-evaluate).
- **Cableado**: `_build_ml` en el composition root (entrena con el Trade Journal
  de la Fase 5), `MLNotifier` en la lista de servicios y 3 jobs del scheduler
  (`ml_nightly_training`/`ml_drift_check`/`ml_meta_evaluation`). Config
  `QE_ML__*` (deshabilitado y sin autoactivación por defecto).

**Calidad:** ruff + mypy strict en verde en todo `app` (347 archivos); **470
tests** (58 nuevos de ML). Verificado end-to-end: el contenedor cablea
`MLEngine`+`MLNotifier`, el notificador arranca como servicio y los 3 jobs del
scheduler se registran; los endpoints responden con un modelo real entrenado y
activo. Se cerró además deuda de calidad previa de la capa ML (1 mypy + 13 ruff).

**Decisiones técnicas:** ADR-050…ADR-057 en `docs/architecture.md` (+ riesgos
Fase 7).

**Nota de numeración:** el ROADMAP había etiquetado el ML como "Fase 8"; se
unifica como **Fase 7** según esta bitácora (cierre de Fase 6 ya lo anticipaba).
El frontend del dashboard queda como fase aparte (pendiente).

**Regla respetada:** el ML nunca abre operaciones ni habilita live; sigue sin
habilitarse live trading; repo sin commitear (commits manuales).

## 2026-07-17 — Fase 8 completada — Dashboard profesional y centro de control

**Categoría:** milestone · **Tags:** `fase-8` `dashboard` `frontend` `command-layer`

**Qué se hizo.** Frontend Next.js en `dashboard/` (App Router + React 19 + TS +
Tailwind v4 + shadcn/ui + TanStack Query + Zustand + Framer Motion + TradingView
Lightweight Charts) con **14 pantallas** sobre la API REST/WebSocket del motor,
tiempo real por `/ws/events`, tema oscuro y responsive. Se añadió la **capa de
comandos** del backend (`app/dashboard/api/`): CORS ampliado a escrituras,
`/api/config` (whitelist), control de estrategias, `/api/backtesting/run|cancel`,
`/api/integrations/{discord,notion}` (webhook enmascarado + test), `/api/logs`,
`/api/reports/*`, `/api/audit`, con **guard anti-live**, **audit log** append-only
y **config store** de overrides. Extras: Workspace System, command palette (⌘K),
atajos y reportes exportables (JSON/MD/CSV).

**Calidad.** Frontend `tsc` + ESLint + Vitest (21 tests) en verde y `next build`
OK (14 rutas); backend ruff + black + mypy strict en verde; la app FastAPI monta
las 55 rutas. Verificado en el navegador en modo degradado (engine apagado): las
pantallas renderizan y degradan limpio (503 / unreachable), sin errores de consola.

**Decisiones técnicas:** ADR-058…ADR-062 (frontend sobre API existente; capa de
comandos + CORS; guard anti-live; audit log JSONL; config por overrides) +
"Riesgos conocidos (Fase 8)" en `docs/architecture.md`; doc `docs/dashboard.md`.

**Pendiente:** aplicar overrides en caliente por subsistema; ejecutar backtests
pesados por HTTP (hoy BacktestLab/CLI); verificación con datos vivos en `paper`
(que emitiría notificaciones Discord reales).

**Regla respetada:** **Live sigue deshabilitado**; ninguna escritura puede
habilitarlo (guard + `resolved_mode()` fuerza `paper`). Toda acción auditada.
Repo sin commitear (commits manuales).

## 2026-07-18 — Fase 9 completada — Producción, DevOps y operación 24/7

**Categoría:** milestone · **Tags:** `fase-9` `produccion` `devops` `seguridad` `notion`

**Qué se hizo.** Se completó la capa de producción (`app/production/`) sobre el
núcleo ya existente (Live Gate, Safe Mode, Kill Switch, Recovery, métricas
Prometheus, health/watchdog, auditoría). Añadido en esta sesión:

- **Notion real con cola de sincronización** (`documentation/backends.py`):
  crea una página por entrada; si Notion cae, encola en disco y `notion_sync`
  reintenta. `ProductionAPI.sync_notion()`.
- **Reportes operativos automáticos** (`production/reporting/`): resumen horario
  y reporte diario (PnL, capital, drawdown, WR, PF, CPU/RAM/latencia, ML,
  brokers, Notion, Discord) al canal lógico `reportes`; `send_daily_report()`.
- **Backups del estado crítico** (`production/backup/`): `tar.gz` + manifiesto
  SHA-256 verificado, rotación, restauración segura; `backup_database()` /
  `restore_database()`.
- **Continuous Improvement Engine** (`production/improvement/`): módulos grandes,
  duplicados, TODOs, módulos sin test y jobs inestables → lista priorizada,
  auditada y documentada en Notion.
- **Discord multicanal** (`notifications/channels/discord_router.py`): webhook
  por canal lógico con ruteo por fuente/nivel; retrocompatible con un webhook.
- **Seguridad** (`app/security/`): rate limiting token-bucket + cabeceras
  (middleware), validación de configuración y vigilancia de rotación de secretos.
- **Failover** (`production/failover/`): arriendo de líder primario/standby,
  fail-closed a standby. **Update/License/Maintenance managers** preparados
  (sin auto-deploy ni restricciones).
- **Wiring**: `_build_production` construye todo e inyecta en `ProductionAPI`;
  nuevos jobs del scheduler (reportes, backup, notion sync, mejora, mantenimiento,
  failover); rutas `/api/{security,backups,updates,maintenance,improvement,notion,
  reports}/*`; Grafana con dashboard provisionado; CI con build de imágenes.

**Calidad.** ruff + mypy strict en verde en todo `app` (429 archivos); **593
tests** (123 nuevos de esta sesión). Verificado end-to-end: el contenedor cablea
la capa completa, los endpoints responden y el middleware de seguridad añade rate
limit + cabeceras. Se cerró deuda de lint/tipos previa de la capa de producción WIP.

**Decisiones técnicas:** ADR-063…ADR-072 + "Riesgos conocidos (Fase 9)" en
`docs/architecture.md`.

**Pendiente:** verificación con Notion/Grafana reales; backup de PostgreSQL vía
`pg_dump` (runbook de infra); paneles de dashboard frontend dedicados (la API y
el estado ya están; se consumen desde las pantallas existentes + endpoints nuevos).

**Regla respetada:** **Live sigue deshabilitado** — `allow_live=False`,
`resolved_mode()→paper`, y ninguna de las piezas nuevas puede abrirlo. Toda acción
auditada; nunca se elimina una auditoría. Repo sin commitear (commits manuales).

## 2026-07-19 — Fase 10: Quant Research Lab, Auto Strategy Generator y evolución autónoma

**Categoría:** milestone · **Tags:** `fase-10` `research` `paper-only`

Construido `app/research/`: laboratorio cuantitativo **independiente de
producción**. Fachada `ResearchLab` (create/generate/optimize/validate/promote/
reject/rank/generate_feature/generate_report/archive). Genoma declarativo +
compilador a `DecisionSource` (composición de bloques auditados, sin codegen).
Strategy Generator por reglas (polaridad + afinidad de filtros). Feature/Factor
Lab validados por coeficiente de información. Optimización multiobjetivo
(escalarización + Pareto) sobre el genético de Fase 6 y Bayesian Lab (TPE sin
dependencias) con historial comparable. Simulation Cluster concurrente. Candidate
Pipeline (BT→WF→MC→ML→benchmark→riesgo) sobre `BacktestLab`. Ranking Engine,
Experiment Manager, Knowledge Base y Candidate Store append-only. **Shadow Mode**
(comparación estadística de Welch challenger vs vigente, sin órdenes). Paper
Validation y Promotion Manager **fail-closed**. `ResearchNotifier` (Discord),
documentación a Notion, rutas `/api/research/*`, cableado en `bootstrap.py`.

**Decisiones:** ADR-073…082 (independencia, genoma+compilador, generador por
reglas, IC para features/factores, multiobjetivo, TPE bayesiano, pipeline, shadow
mode, promoción fail-closed, stores append-only).

**Verificación:** +48 pruebas nuevas; suite total en verde; `ruff`+`black`+
`mypy --strict` limpios en `app` (454 archivos). Cada bloque se probó de extremo a
extremo (generar→validar→rankear, optimizar, shadow, promover).

**Pendiente:** pantallas Next.js dedicadas del research dashboard (la API ya
está); backfill histórico real para correr el laboratorio con datos de mercado;
habilitar la revisión ML del pipeline cuando haya historial de operaciones.

**Regla respetada:** el laboratorio **no opera** y **nunca** habilita live —
`allow_live=False`, `resolved_mode()→paper`—; la promoción exige aprobación
humana. Repo sin commitear (commits manuales del usuario).

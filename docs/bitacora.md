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

## 2026-08-03 — Bloque 1: holding time por estrategia (y el artefacto de los 4s)

**Categoría:** fix · **Tags:** `ejecucion` `regimen` `holding` `evaluacion-continua`

**Diagnóstico.** El evaluador continuo daba +0.26R en señales virtuales y la
ejecución real −0.15R. `regime_change_min_holding_seconds` era un valor **global**:
un promedio que no le sirve a ninguna estrategia. `order_block` necesita ~1950s
para resolver su tesis y `bos` ~135s; con un único umbral, o se corta al primero
antes de tiempo o se deja al segundo sin salida útil por régimen.

**Investigación del `choch` de 4s (punto 2 del bloque) — era un artefacto real.**
`PerformanceTracker.evaluate_open` filtraba las velas con `c.end > opened_at`, lo
que dejaba entrar la vela **en curso** en el momento de la señal. El rango
high/low de esa vela incluye precio **anterior** a la señal, así que la operación
virtual se resolvía contra movimiento que ya había ocurrido, y `closed_at` era el
cierre de esa misma vela: de ahí una duración media de ~4s y una expectativa
inflada. No era específico de `choch` — contaminaba a toda estrategia que dispara
tarde dentro de la vela; `choch` sólo era la más expuesta.

**Cambios.**
- `PerformanceTracker.evaluate_open` sólo resuelve contra velas que **empiezan**
  después de la entrada (`c.start >= opened_at`). Las métricas del evaluador
  vuelven a medir sólo lo que la señal pudo prever.
- Atribución de estrategia extremo a extremo: `Decision.primary_strategy` /
  `primary_category` (derivadas de las contribuciones del consenso, deterministas)
  → `DecisionGenerated.strategy`/`strategy_category` (viaja por el bus, la
  ejecución no conoce los plugins) → `Position` → `TradeRecord`. Vacías cuando no
  se puede atribuir (p. ej. posiciones adoptadas del broker al arrancar).
- `ExecutionSettings.min_holding_seconds_for(strategy, category)`: fallback en
  tres escalones — valor por estrategia → por categoría → global. Semillas
  tomadas de la duración media del evaluador continuo.
  **`choch` se deja sin valor propio a propósito** hasta tener muestra limpia
  post-fix; cae a su categoría (`smc`, 900s).
- El umbral aplicado a cada posición queda en `context_snapshot.min_holding_seconds`
  del Trade Journal: sin eso no se puede auditar si una salida por régimen
  respetó el holding por estrategia o cayó al fallback.
- Whitelist del Config Center y `.env.example` actualizados (las dos tablas
  nuevas aplican **en caliente**, como el valor global).

**Intacto.** El límite de 4h (`max_holding_minutes`) se sigue evaluando **antes**
que la salida por régimen: la red de seguridad global no se toca.

**Deuda preexistente cerrada de paso (Bloque 7.4).** Los 3 errores de mypy en
`app/cache/redis_backend.py` **no eran del código**: el venv tenía instalado
`types-redis 4.6` (stubs obsoletos, deprecados desde que redis-py trae los suyos)
que shadoweaba los tipos inline de `redis 8.0`. Desinstalado → los 3 desaparecen
sin tocar el módulo. Cerrado también el `noqa: BLE001` inútil de
`app/market/feed/feed.py:169`.

**Tests.** 13 nuevos: resolución del umbral (propio / categoría / global /
mayúsculas / `choch` sin valor propio), no-corte de `order_block` frente a corte
de `bos` en la misma situación, viaje de la atribución hasta el journal,
prioridad del límite de 4h, auditoría del umbral aplicado, y dos de regresión del
evaluador (la vela en curso no resuelve; la posterior sí). Suite: **760 en verde**;
`ruff` + `black` + `mypy --strict` limpios en todo el repo.

**Arquitectura.** Sin acoplamiento nuevo: la atribución viaja por el Event Bus,
no por llamadas directas. Sin código de fases futuras. Ningún camino, directo ni
indirecto, hacia habilitar live.

**Qué esperar.** Menos salidas por `regime_change` en las estrategias de tesis
larga (smc/volume) y una duración mediana que sube hacia lo esperado por
estrategia. La falsación automática de esto es el Bloque 7.1.

## 2026-08-03 — Bloque 2: experimentos con fecha de corte por estrategia

**Categoría:** feature · **Tags:** `ejecucion` `estrategias` `discord` `decision-asistida`

**Diagnóstico.** `atr_expansion` y `mean_reversion` llevan R negativo consistente,
pero la decisión de apagarlas dependía de que el operador se acordara de revisar
los números tres días después de un cambio. Eso no escala y se olvida.

**Propuesta implementada.** Un experimento con fecha de corte por estrategia:
al vencer la ventana se mide la expectativa con las operaciones cerradas
**dentro de la ventana**, y el sistema emite un veredicto.

- `deactivation_candidate`: muestra suficiente y expectativa bajo el umbral →
  aviso a Discord con los números y la propuesta.
- `passed`: se recuperó → experimento cerrado sin acción.
- `extended`: no hubo operaciones suficientes (< `min_trades`) → la ventana se
  alarga en vez de emitir un veredicto con 3 operaciones, que sería ruido.

**Regla dura respetada (punto 2 del bloque).** El mecanismo **no desactiva
nada**. Publica `StrategyExperimentVerdict`, el `ExecutionNotifier` lo convierte
en embed, y apagar la estrategia sigue siendo mover `strategies_enabled` a mano.
Hay un test que lo fija explícitamente.

**Por qué la ventana empieza con el experimento.** El experimento juzga a la
estrategia **bajo las reglas nuevas** (holding por estrategia del Bloque 1).
Contar operaciones anteriores al cambio mediría justo lo que el cambio pretendía
arreglar. Test dedicado.

**Toggle por estrategia.** `execution.strategies_enabled`, con la misma semántica
que `symbols_enabled`: bloquea **sólo la apertura**. La estrategia sigue emitiendo
señales y votando en el consenso, así que su historial no se corta y se puede
medir qué habría hecho de haber operado. Una decisión sin atribución nunca se
bloquea. En la whitelist del Config Center (aplica en caliente).

**Persistencia.** Registro append-only en
`data/execution/strategy_experiments.jsonl`. Sin rehidratación, cada reinicio
del motor reiniciaría el reloj y la fecha de corte no llegaría nunca; el estado
resultante (incluida una fecha de corte ya extendida) viaja con cada veredicto.
Tres tests cubren el reinicio.

**Cableado.** `StrategyExperimentManager` en el composition root, inyectado en el
`ExecutionEngine`; job `strategy_experiment_check` en el scheduler (1h,
`run_immediately`), detrás de `execution.experiments.enabled`.

**Tests.** 15 nuevos. Suite: **775 en verde**; `ruff` + `black` + `mypy --strict`
limpios.

**Arquitectura.** El gestor no conoce el Event Bus ni Discord: devuelve
veredictos y el motor los publica — testeable en aislamiento y sin acoplar la
capa de notificación. Sin funcionalidad de fases futuras. Ningún camino hacia
live.

## 2026-08-03 — Bloque 3: el Meta Strategy Manager gobernando de verdad

**Categoría:** fix · **Tags:** `ml` `meta-strategy` `gobierno` `auditoria` `fase-7`

**Diagnóstico.** El MSM "existía pero no gobernaba nada útil" por **dos** motivos
independientes, no uno:

1. **No veía estrategias.** `StrategyIntelligence.label_trades` leía la
   estrategia de `context_snapshot["strategy"]`, que nadie rellenaba nunca. Su
   propio docstring lo anticipaba: *"cuando la ejecución rellene el snapshot con
   la estrategia dominante, esto queda cableado sin cambios"*. Como no se
   rellenaba, **todas** las operaciones caían al default `"portfolio"` y el MSM
   recibía una única entrada agregada — de ahí el `weights: {"portfolio": 1.04}`.
2. **Nadie aplicaba sus decisiones.** `StrategyWeightsUpdated` sólo tenía un
   suscriptor: el notificador de Discord. Los pesos que usaba el consenso
   seguían siendo los de configuración, fijos desde el arranque. El MSM
   calculaba, publicaba, avisaba... y no cambiaba nada.

**Cambios.**
- `label_trades` prefiere ahora `trade.strategy` (el campo de primera clase que
  anadio el Bloque 1), con `context_snapshot` como compatibilidad para el
  journal antiguo y `"portfolio"` solo para lo genuinamente no atribuible
  (posiciones adoptadas del broker al arrancar).
- **`MetaGovernanceApplier`** (`app/engine/meta_governance/`): servicio que
  escucha `StrategyWeightsUpdated` y `MetaStrategyDecision` y los traduce a
  `StrategyEngine.set_weight` / `enable_strategy` / `disable_strategy`. Nuevo
  `StrategyEngine.set_weight`, que refleja el peso tambien en `stats` para que
  el dashboard muestre el vigente y no el de arranque.
- `MLEngine.run_meta_evaluation` publica ademas una `MetaStrategyDecision` por
  cada `disable`/`enable`: el aplicador necesita saber *que* estrategia y por
  que, no solo el mapa de pesos.
- **Evidencia mixta (punto 1 del bloque).** El MSM combina las dos fuentes por
  estrategia: el Trade Journal (Fase 5, lo que la ejecucion capturo) y el
  evaluador continuo (Fase 4, la calidad de la senal en si). El segundo pesa
  `1 - trades/min_trades` mientras la muestra ejecutada sea escasa: 4
  operaciones cerradas no son evidencia para mover un peso, pero esa misma
  estrategia puede tener cientos de senales resueltas. `VirtualStrategyStats` se
  declara en la capa de ML y el composition root adapta el `PerformanceTracker`,
  para que ML no dependa de `app.engine.evaluation`.

**Limites deliberados.**
- **La evidencia virtual nunca desactiva** una estrategia: no incluye costes,
  slippage ni salidas por regimen. Solo influye en el peso. Test dedicado.
- El aplicador solo mueve **configuracion**. No abre ni cierra posiciones, no
  toca codigo y no puede habilitar live: no conoce la ejecucion ni el Live Gate.
- **Todo cambio efectivo se audita** (append-only, con peso anterior, nuevo,
  actor `meta_strategy_manager` y motivo). Un peso que cambia solo y sin rastro
  es indistinguible de un bug. Un peso sin cambio real no ensucia la auditoria.
- `ml.meta.apply_governance=false` deja el aplicador en **modo observacion**:
  registra lo que habria hecho sin tocar nada. Util para mirar al MSM antes de
  dejarle gobernar.

**Tests.** 15 nuevos. Suite: **790 en verde**; `ruff` + `black` + `mypy --strict`
limpios.

**Arquitectura.** Todo por el Event Bus: el MSM no conoce al Strategy Engine ni
al reves. Sin funcionalidad de fases futuras. Ningun camino hacia live.

## 2026-08-03 — Bloque 4: sanear los datos de entrenamiento del ML

**Categoria:** feature · **Tags:** `ml` `training-set` `eras` `etiquetas` `auditoria`

**Diagnostico.** El ML entrena con el historial que genera el propio motor, y ese
historial arrastra bugs de ejecucion ya arreglados. Entrenando sin distinguirlos,
el modelo no aprende "esta senal es mala": aprende "esta senal es mala **porque
la ejecucion la saboteo**", y acaba penalizando contextos que si tenian edge.

**Defensa 1 — segmentacion y ponderacion por era** (`app/ml/datasets/eras.py`,
configurable en `ml.data_quality`). Cada operacion se clasifica por su **hora de
entrada** (lectura conservadora: una operacion abierta antes de un fix corrio
bajo las reglas viejas casi toda su vida, aunque cerrara despues):

| Era | Hasta | Peso | Criterio |
| --- | --- | --- | --- |
| `pre_contract_size_y_familias_regimen` | 2026-07-27 | 0.0 (excluida) | Stop mal calculado: su R **no mide la senal, mide un stop equivocado**. No es muestra floja, es medicion invalida. |
| `pre_trailing_activate_r` | 2026-07-29 | 0.35 | Sesgo real pero **acotado y direccional**; la entrada y su contexto siguen siendo validos. |
| `post_fixes` | — | 1.0 | Historial limpio. |

Las eras son **declarativas**: cuando se arregle el proximo bug de ejecucion
basta anadir una entrada a la configuracion, sin fechas incrustadas en codigo.

**Defensa 2 — etiqueta dual (punto 2b del bloque).** Se separan dos preguntas
que no son la misma:
- `win`/`rr_positive`/`not_stopped` -> **calidad de ejecucion** (todas las salidas).
- `signal_quality` -> **calidad de la senal**: solo cuentan las operaciones cuyo
  cierre **resolvio la tesis** (objetivo/stop/trailing/BE). Las que cerro la
  ejecucion (regimen, tiempo, kill switch, manual) se descartan: esa operacion
  nunca puso a prueba su propia tesis, y etiquetarla como "senal mala" es
  exactamente el error que este bloque evita.

Cada dataset declara que mide en `metadata["label_measures"]`.

**Correccion de alineacion.** `Dataset.subset` no cortaba los vectores por fila:
tras un split, cada muestra habria heredado el peso de otra. Ahora
`sample_weights`/`sample_eras` se cortan con las filas. Test dedicado.

**Auditoria (punto 3).** `dataset.metadata["era_breakdown"]`,
`MLEngine.data_quality_report()` y `/api/ml/status` responden "que datos entraron
al entrenamiento y por que" sin releer el journal. Cada exclusion lleva su motivo
escrito; hay un test que exige que no este vacio. Documentado en `docs/ml.md`.

**Limitacion documentada, no disimulada.** La fuente de verdad ideal para la
calidad de senal es el evaluador continuo, pero **hoy no se puede unir fila a
fila** con el Trade Journal: el evaluador guarda estadistica agregada por
estrategia, no el resultado virtual de cada senal, y el `TradeRecord` lleva
`decision_id` pero no los `signal_id` que lo originaron. Por eso
`signal_quality` se aproxima desde el propio journal filtrando por motivo de
salida — aproximacion honesta y sin lookahead, pero sigue midiendo operaciones
ejecutadas. Cerrar el hueco exige (1) persistir el resultado virtual por
`signal_id` y (2) propagar los `signal_id` hasta el `TradeRecord`. Anotado en
`docs/ml.md` como pendiente explicito.

**Nota sobre los fixtures.** Los de test se movieron de 2026-01-01 a 2026-08-01:
con las fechas antiguas caian en la era excluida y la suite se quedaba sin datos.
Es la senal de que el filtro funciona.

**Tests.** 14 nuevos, incluido el que pide el punto 4 del bloque
(`test_a_trade_from_a_buggy_era_never_enters_with_the_same_weight_as_a_clean_one`).
Suite: **804 en verde**; `ruff` + `black` + `mypy --strict` limpios.

**Arquitectura.** Sin acoplamiento nuevo; el saneamiento vive en la capa de
datasets del ML. Ningun camino hacia live.

## 2026-08-03 — Bloque 5: gate de vigencia del ML frente a las reglas de ejecucion

**Categoria:** feature · **Tags:** `ml` `gate` `reentrenamiento` `discord`

**Diagnostico.** `min_cv_auc` evita **activar un modelo malo**. Falta detectar
algo distinto: que un modelo **bueno** dejo de ser representativo porque
cambiaron las reglas bajo las que se entreno. Nada en sus metricas lo delata —
su AUC de validacion sigue siendo el mismo numero de ayer — asi que sigue
asesorando con la estadistica de un motor que ya no existe.

Es un riesgo inmediato, no teorico: el Bloque 1 acaba de cambiar el holding.

**Solucion.** `app/ml/monitoring/execution_rules.py` calcula un **hash de las
reglas de ejecucion significativas**, agrupadas en cuatro familias (las que
nombra el bloque): `holding`, `trailing`, `sizing`, `risk`, mas `toggles` de
simbolos/estrategias. La huella se congela junto al modelo al registrarlo
(`ModelRecord.execution_rules_hash` + instantanea legible) y se compara con la
vigente.

**Lo que queda deliberadamente fuera de la huella:** cadencias, rutas de fichero
e intervalos de reporte. Una alerta que salta por cambios inocuos entrena al
operador a ignorarla. Hay un test que fija esto.

**Cuatro estados**, no dos:
- `ok` — el modelo se entreno con las reglas vigentes.
- `stale` — cambiaron; **requiere reentrenamiento**. La alerta dice **que familia
  de reglas** cambio, no solo que algo cambio.
- `unknown` — modelo registrado antes de que existiera este control. No se
  afirma que este obsoleto, solo que no consta: se sugiere reentrenar para fijar
  la huella.
- `no_model` — nada que validar.

**Regla dura respetada (punto 2 del bloque).** El gate **alerta y sugiere**, y
nunca desactiva el modelo, activa otro ni dispara un reentrenamiento automatico.
Un reentrenamiento automatico ante cualquier cambio de configuracion seria una
via comoda para que el motor se reentrene solo sobre datos que aun no existen.
El test `test_a_stale_model_raises_an_alert_and_stays_active` comprueba que tras
la alerta el modelo sigue activo y con el mismo id.

**Cableado.** Job `ml_execution_rules_check` (1h, `run_immediately`), evento
`ModelRequiresRetraining` y su embed de Discord — que incluye explicitamente
"Accion tomada: ninguna (el ML asesora, no decide)".

`RULES_VERSION` permite invalidar todas las huellas a proposito si cambia *que*
reglas se consideran significativas.

**Tests.** 13 nuevos. Suite: **817 en verde**; `ruff` + `black` + `mypy --strict`
limpios.

**Arquitectura.** El gate solo diagnostica y publica un evento; Discord reacciona
desacoplado. Ningun camino hacia live.

## 2026-08-03 — Bloque 6: Research Lab al scheduler con presupuesto de CPU

**Categoria:** feature · **Tags:** `research` `fase-10` `scheduler` `cpu` `presupuesto`

**Diagnostico.** El ciclo autonomo ya existia (`auto_cycle`), pero **sin ningun
techo**: ni ventana horaria, ni tope de trabajo por ejecucion, ni timeout. Un
ciclo de generacion son cientos de backtests, y comparte VPS con el bucle de
gestion de posiciones, que corre cada 2s y es el unico que no puede llegar tarde.

**Tres limites duros** (`app/research/budget.py`, `settings.research.budget`):
- **Ventana horaria** 01:00-05:00 UTC por defecto — entre el cierre americano y
  la apertura europea. `start == end` = siempre abierta (forma explicita de
  quitar solo la restriccion horaria).
- **Tope de trabajo**: 2 simbolos x 12 genomas. `max_generated_per_run` es la
  variable que mas multiplica el numero de backtests.
- **Timeout duro** de 900s con `asyncio.wait_for`: un ciclo colgado no puede
  seguir consumiendo CPU hasta el disparo siguiente. Los candidatos ya
  registrados se conservan.

**Dos vetos de cortesia**: no arrancar con posiciones abiertas (el laboratorio
puede esperar; una posicion viva, no) ni con la CPU por encima del 70%. Un
sensor de CPU que no reporta **no bloquea** — misma regla que Safe Mode.

**Decision de diseno.** `budget.enabled=false` **deniega**, no relaja: un
laboratorio sin techo en la VPS que opera es justo lo que se evita. Test dedicado.

**Punto 4 respetado: no se activa nada.** `auto_cycle` sigue en `false` por
defecto. Hay un test que lo fija.

**Puntos 1 y 2 — documentacion, sin decidir por el usuario.** `docs/research.md`
lleva ahora el consumo estimado (24 pipelines/ciclo, ~2 vCPU saturados durante
como mucho 15 min, una vez al dia) y el analisis **misma VPS vs otra maquina**
con pros y contras de ambas. La recomendacion se deja como lectura, no como
decision: la misma VPS es razonable para acumular la primera evidencia; mover
tiene sentido cuando el cuello de botella sea el research y no la falta de
experimentos.

> El consumo es una estimacion de orden de magnitud, **no medida en la VPS**.
> Hay que verificarla con una ejecucion real antes de dejarlo activo.

**Punto 3 intacto.** La promocion sigue exigiendo aprobacion humana
(PromotionManager fail-closed); nada de esto cambia con el ciclo activado.

**Bug preexistente cerrado de paso.** `app/dashboard/api/routes/system.py`
importaba `QuantEngine` al nivel superior, cerrando un ciclo
`engine.engine -> dashboard.api -> routes.system -> engine.engine`. Cualquier
proceso que importara `app.engine.engine` primero fallaba — es decir, el fallo
dependia del **orden de importacion**, no del codigo, y los tests que tocaban el
motor no se podian ejecutar en aislamiento. Import diferido dentro de la funcion.

**Tests.** 15 nuevos. Suite: **832 en verde**; `ruff` + `black` + `mypy --strict`
limpios.

**Arquitectura.** El presupuesto decide y explica; el motor aplica. Se prueba sin
levantar el laboratorio. Ningun camino hacia live.

## 2026-08-03 — Bloque 7: robustez adicional

**Categoria:** feature+fix · **Tags:** `falsacion` `sizing` `deuda` `oro`

### 7.1 — Falsacion automatica del cambio del Bloque 1 (implementado)

"Se desplego sin errores" no es evidencia de que un cambio funcione. El Bloque 1
hizo tres predicciones concretas y `app/execution/falsification.py` las mide
solo en su ventana (48h por defecto):

1. `take_profit` **sube del 0 %** — si ninguna operacion llega al objetivo, el
   holding sigue cortando la tesis antes de tiempo.
2. `regime_change` **baja del 80 %** — era el sintoma original.
3. La **duracion mediana** se acerca a la esperada **por estrategia** (no a un
   numero global: `bos` espera 135s y `order_block` 1950s, y comparar contra un
   promedio no diria nada).

Decisiones que importan:
- El veredicto se publica **acierte o falle**. Una prediccion que solo se reporta
  cuando se cumple no es una falsacion, es una felicitacion. Test dedicado.
- Muestra insuficiente **extiende la ventana** en vez de concluir con ruido.
- Solo se exige que la duracion **no se quede corta**: pasarse de largo ya lo
  acota el limite global de 4h, y no era el fallo que este cambio corregia.
- El modulo no cambia ninguna configuracion. Solo mide y publica.

Cableado: job `holding_falsification_check` (1h), evento `HoldingChangeFalsified`
y su embed de Discord. Estado persistido, asi que un reinicio no reinicia la
ventana ni repite el veredicto.

### 7.3 — Sizing frente al crecimiento del capital (documentado)

Nueva seccion en `docs/architecture.md` con los hitos de revision (~$1 000,
~$2 000-5 000, ~$20 000) y los cuatro parametros que hay que revisar **juntos**,
porque se calibraron juntos. El punto de fondo, escrito explicitamente: el tope
alto **no expresa apetito de riesgo, expresa una restriccion de granularidad del
broker** (que el lote minimo quepa). Por eso envejece mal — deja de ser necesario
mucho antes de dejar de estar configurado, y el modo de fallo no es un error sino
riesgo silencioso.

### 7.4 — Deuda preexistente (cerrada en el Bloque 1)

- Los 3 mypy de `app/cache/redis_backend.py` **no eran del codigo**: `types-redis`
  4.6 (stubs deprecados) shadoweaba los tipos inline de redis 8.0.
- Cerrado el `noqa: BLE001` inutil de `app/market/feed/feed.py:169`.
- `black` no estaba instalado en el venv y habia 9 ficheros con drift de formato;
  instalado con el pin del repo (26.5.1) y formateado.

### 7.2 — Churn del oro: PENDIENTE, y no por olvido

Los 72 cierres/dia del oro habia que retomarlos **una vez aplicado el Bloque 1**,
porque el holding por estrategia probablemente los cambie. El Bloque 1 esta
implementado pero **no desplegado**, asi que todavia no hay datos post-cambio que
medir: cualquier conclusion ahora seria sobre el regimen viejo.

El instrumento para medirlo ya existe: la falsacion del 7.1 mide exactamente la
mezcla de salidas y la duracion mediana. Cuando el veredicto llegue, el churn del
oro se lee de ahi. Queda anotado como el siguiente paso tras el despliegue.

**Tests.** 14 nuevos. Suite: **846 en verde**; `ruff` + `black` + `mypy --strict`
limpios en todo el repo.

## 2026-08-04 — INCIDENTE: el motor lleva 4 dias sin operar (reloj congelado)

**Categoria:** incident · **Tags:** `produccion` `reloj` `backtesting` `websocket` `postmortem`

### Sintoma

Spam de alertas y **cero operaciones** desde el 31/07. El motor no estaba caido:
respondia a la API, tenia 0 posiciones abiertas y `app.log` rotaba **10 MB cada
35 minutos**.

### Causa raiz: el reloj de replay se filtro al motor en vivo

`app/utils/time.py` guardaba el proveedor de tiempo en un **global de modulo**, y
el `BacktestLab` corre en el **mismo proceso y el mismo event loop** que el motor.
El 2026-07-31 a las 16:41 UTC un backtest instalo el reloj simulado con
`use_clock(...)` y su bloque nunca llego a cerrarse (tarea colgada o cancelada sin
desenrollar). `backtesting/status` mostraba `experiments: 0`, coherente con
"arranco y no termino".

A partir de ahi `utc_now()` devolvia siempre `2026-07-31T16:41:00`. Todas las
lineas de log llevaban ese timestamp, identico al segundo, durante 4 dias.

**Por que dejo de operar:** el validador de mercado compara el timestamp de cada
tick contra `utc_now()`. Con el reloj 3,5 dias atrasado, **todo** tick del broker
parecia venir del futuro:

```
Data quality [ETHUSDM] future_timestamp:
exchange_ts=2026-08-04T04:04:55 > local+0:00:05 (discard=True)
```

Descarte del 100% de los ticks -> sin velas -> sin señales -> sin decisiones ->
sin operaciones. **Ninguna alarma salto porque el motor no estaba caido: estaba
ciego**, y ninguna metrica reflejaba la hora interna.

**Arreglo.** El proveedor pasa a un `ContextVar`. El reloj simulado alcanza solo
a la tarea que lo instala y a las que ella crea — el alcance real de un backtest —
y las tareas del motor en vivo siguen viendo el reloj de pared **pase lo que pase
con el bloque**. 11 tests nuevos, incluidos el de tareas hermanas y el del bloque
que nunca se cierra (ambos fallaban con la implementacion anterior).

**Red de seguridad.** `HealthMonitor` mide ahora `clock_skew_seconds` (reloj
efectivo vs. reloj de pared, via `wall_now()`, que ignora el inyectado). Una
desviacion por encima de `health.max_clock_skew_seconds` (5s) marca el sistema
**UNHEALTHY**, no degradado: con el reloj mal el motor deja de operar en silencio.

### Segundo bug, independiente: fuga de suscripciones en el WebSocket

El 75% de las lineas de log eran `asyncio | WARNING | socket.send() raised
exception.`. CPython lo emite en `proactor_events.py` cuando se escribe sobre un
transporte con `_conn_lost`, a partir del 5º intento — y **no lanza excepcion**,
descarta y vuelve.

El endpoint `/ws/events` era un `while True` que solo salia con
`WebSocketDisconnect`. Cuando el cliente desaparece sin cierre limpio (pestaña
cerrada, red caida, dashboard reiniciado) `send_json` no lanza, asi que el bucle
seguia consumiendo eventos y "enviandolos" a un socket muerto indefinidamente:
un WARNING por cada evento del motor y **una suscripcion al bus que nunca se
liberaba**. Cada recarga del dashboard dejaba otro zombi acumulando ruido.

**Arreglo.** El envio va a una tarea aparte y el endpoint se queda en
`websocket.receive()`, que es la unica señal fiable de desconexion en un canal
de solo lectura. 2 tests de regresion sobre el contador de suscriptores del bus.

### Deuda de diseño anotada

El motor estuvo 4 dias sin operar y **nada aviso**. El `clock_skew` cubre esta
causa concreta, pero no la clase de fallo: convendria una alarma sobre la **tasa
de descarte del validador** (100% sostenido = ciego) y sobre **ausencia de
señales** en ventana de mercado abierto. Pendiente, no hecho.

## 2026-08-04 — Alarmas de pipeline: motor ciego y motor mudo

**Categoria:** feature · **Tags:** `monitorizacion` `alarmas` `postmortem`

Cierre de la deuda que dejo el incidente del reloj congelado. El problema de
fondo no era el reloj: era que **el motor estuvo 4 dias sin operar y nada aviso**.
El proceso respondia a la API, todos los servicios figuraban `running` y el
watchdog de componentes no tenia nada que decir. El motor no estaba caido: habia
dejado de ver el mercado, que no se parece a un fallo en ninguna metrica.

`health.max_clock_skew_seconds` cubre *aquella causa*. Estas dos alarmas cubren
el **efecto**, venga de donde venga (un reloj, un proveedor que cambia el formato
de timestamp, un simbolo mal mapeado, un despliegue a medias):

- **Ciego** (`MarketDataBlind`): entran datos y se descarta >= 95% en la ventana.
- **Mudo** (`SignalDrought`): entran datos **limpios** y no sale ni una señal
  durante N ventanas consecutivas (30 min por defecto).

**Decisiones de diseño.**
- Se mide por **deltas entre muestras**, no sobre contadores acumulados: un
  acumulado diluye el presente y, tras un incidente largo, seguiria en rojo
  mucho despues de haberse recuperado.
- La alarma de "mudo" **exige datos limpios fluyendo**, y por eso no necesita un
  calendario de sesiones: con el mercado cerrado no hay ticks, no se cumple la
  condicion y no avisa. Un calendario habria que mantenerlo y se equivocaria en
  festivos; el flujo de datos es evidencia directa de que el mercado esta vivo.
- **Latch en ambas**, con aviso de recuperacion. Una alarma que se repite cada 5
  minutos se acaba silenciando, y una alarma silenciada es peor que ninguna.
- La primera pasada solo fija linea base: comparar contra cero al arrancar daria
  un falso positivo garantizado en cada reinicio.
- Estar ciego **no** dispara tambien "mudo" (sin datos limpios esa alarma no
  aplica): confundirlas despistaria el diagnostico.
- Lee contadores que ya existian (`DataValidator.stats`, `StrategyStats
  .signals_produced`); no toca el camino caliente de los datos.

**Cableado.** `PipelineWatchdog` en el composition root y en la lista de
servicios; avisa por Discord y publica en el bus. `settings.pipeline_watch`.

**Tests.** 13 nuevos, incluido el escenario exacto del incidente (100% de
descarte) y el del mercado cerrado, que no debe avisar. Suite: **879 en verde**.

## 2026-08-04 — Bloque 8: cerrar el loop señal→ejecución (`signal_id` end-to-end)

**Categoria:** feature · **Tags:** `ml` `trazabilidad` `evaluacion-continua` `join` `bloque-8`

**Diagnostico.** La cadena estaba rota en menos sitios de los que parecia.
`Decision.signals_considered` **ya** llevaba la tupla de `signal_id` desde la
Fase 3; el corte estaba en `DecisionGenerated`, que no la publicaba, y en
`PerformanceTracker._resolve`, que agregaba en `StrategyPerformance` y hacia
`pop()` — descartando el resultado individual de cada senal. Son exactamente los
dos huecos que el Bloque 4 dejo declarados, ni uno mas.

**Propagacion.** `signal_ids` como campo en `DecisionGenerated` → `OrderRequest`
→ `Position` → `TradeRecord`, con default vacio y `.get()` en `from_dict`: el
journal anterior al bloque se sigue releyendo sin migracion. Mismo patron que la
atribucion de estrategia del Bloque 1 — campo del evento, no acoplamiento.

**Store por senal.** `VirtualOutcomeStore` (`app/engine/evaluation/outcomes.py`),
append-only en `data/performance/virtual_outcomes.jsonl`, escritura por lotes
(50) mas volcado en la cadencia del snapshot y al parar. Es opcional en el
tracker: sin el, el evaluador se comporta como antes del bloque.

**El join.** `app/ml/datasets/join.py`. Con varias senales por operacion toma la
**media** de sus R: la decision es multi-estrategia por diseno y quedarse con
una sola seria atribuir a una lo que votaron varias.

**Lo que el join cambia de verdad.** La aproximacion por motivo de salida tenia
un punto ciego estructural: **descartaba las operaciones que la ejecucion
corto** (regimen, tiempo, kill switch) — justo el caso que habia que medir. El
evaluador si las resuelve, contra precio posterior a la senal. Hay dos tests
enfrentados que lo fijan: la misma operacion cortada por regimen entra al
dataset con etiqueta positiva via join, y sigue descartandose sin el.

**Tres casos sin match, contados.** `unmatched_legacy` (cae al camino antiguo;
hoy es casi todo el historial), `unmatched_unresolved` (fuera de la etiqueta de
senal, dentro de las de ejecucion) y `signal_without_trade` (senal filtrada o
vetada por riesgo, con desglose por estrategia). En `join_breakdown`, con el
mismo estandar que el `era_breakdown` del Bloque 4. `labelled_from_join` dice
cuantas etiquetas vinieron de evidencia directa y cuantas de la aproximacion.

**Bug preexistente cerrado de paso.** El snapshot de recuperacion
(`app/production/recovery/snapshot.py`) no persistia `strategy` ni
`strategy_category`: una posicion restaurada tras un reinicio perdia la
atribucion del Bloque 1, caia al holding **global** en vez del suyo por
estrategia, y su operacion llegaba al journal sin nada que unir. Test dedicado.

**Arquitectura.** El ML declara su propio `SignalOutcome` y el composition root
adapta el store, para no depender de `app.engine.evaluation` — misma regla que
ADR-087. El saneamiento por era se aplica **despues** del join. Ningun camino
hacia live.

**Tests.** 19 nuevos. Suite: **898 en verde**; `ruff` + `black` + `mypy --strict`
limpios. ADR-094. `docs/ml.md` reescrito: la seccion "Limitacion conocida
(pendiente)" se sustituye por la metodologia nueva.

**Pendiente que este bloque NO cierra.** El journal de produccion acumulado
(1209 operaciones al 04/08) no tiene `signal_ids`: la trazabilidad completa
empieza a acumularse desde el despliegue, no retroactivamente.

## 2026-08-04 — Bloque 11: endurecer el despliegue (auditoria + guard de arranque)

**Categoria:** feature+fix · **Tags:** `produccion` `aislamiento` `postmortem` `guard` `bloque-11`

**Diagnostico.** El incidente del reloj fue el sintoma de que backtesting,
research y motor en vivo comparten proceso, event loop y espacio de modulos. Se
auditaron los patrones equivalentes; la tabla completa con veredicto por sitio
esta en `docs/architecture.md` ("Riesgos de aislamiento de procesos").

Resumen: el unico riesgo real equiparable es el `_LOOP` de
`app/backtesting/quant_source.py` — un event loop en hilo daemon que el
backtesting crea y **nunca cierra**. No corrompe estado del motor (el motor no
lo usa), pero su presencia es evidencia de que un backtest corrio en el proceso,
y el guard la aprovecha como senal. El `_buffer` de logging y el `_BACKGROUND`
de `signal_engine` son el mismo patron con consecuencia inocua. El caso sin
solucion a este nivel es `MetaTrader5`, que es singleton **de proceso** por
diseno de la libreria: motor y backtest comparten terminal a la fuerza.

**Sospecha investigada que resulto infundada.** `QuantCoreDecisionSource` corre
su pipeline en otro hilo (`run_coroutine_threadsafe`), lo que sugeria que el
reloj simulado no llegaria y que los backtests usarian hora de pared en
silencio. Se comprobo empiricamente: **si llega** — `call_soon_threadsafe` copia
el contexto del hilo llamante. Queda escrito para no repetir la sospecha.

**Guard de arranque** (`app/engine/startup_guard.py`, ADR-095), invocado al
principio de `QuantEngine.start()`. Tres comprobaciones: desviacion del reloj
efectivo vs. el de pared, instrumentacion de test cargada (`pytest` en
`sys.modules` / `PYTEST_CURRENT_TEST`) y event loop de backtesting ya activo.

**Aborta, no degrada.** Un warning habria devuelto el modo de fallo del
incidente: operar mal en silencio mientras todo figura `running`. Un motor que
no arranca se ve en el primer minuto; uno ciego tardo cuatro dias.

**Solo vigila `paper` y `production`.** En `development`/`testing` la
instrumentacion de test es lo normal —la propia suite arranca el motor— y
abortar alli convertiria el guard en un estorbo; un guard que estorba se acaba
desactivando. Informa y no bloquea. Hay test de las dos mitades, incluido el de
que un arranque limpio **no** se bloquea: el fallo esperable de un guard asi es
el falso positivo.

**Lo que el guard no puede hacer.** Enumerar monkeypatch en tiempo de ejecucion.
Por eso detecta el **entorno que los produce**, no los parches.

**Linux + Docker vs Windows (punto 3) — respuesta corta: no migrar ahora.** El
incidente fue estado compartido dentro de un unico proceso Python; eso ocurre
igual en Linux y en Docker, que aislan entre contenedores, no dentro de uno.
Migrar no lo habria cortado. Ademas la dependencia MT5↔Windows convierte la
migracion en un cambio de broker encubierto — decision de capital, no tecnica.
El beneficio buscado (aislar backtest del motor) sale mas barato con la opcion
recomendada: **backtest/research en proceso hijo**, propuesta y documentada sin
implementar, como pedia el bloque.

**Tests.** 10 nuevos. Suite: **908 en verde**; `ruff` + `black` + `mypy --strict`
limpios. ADR-095 y seccion de auditoria en `docs/architecture.md`.

**No se toco `qevps`.**

## 2026-08-04 — Bloque 10: Research Lab listo para activar (sin activar)

**Categoria:** feature · **Tags:** `research` `rollback` `activacion-gradual` `bloque-10`

**Punto 1 — que cambio desde el Bloque 6.** El Research Lab **no** causo el
incidente del reloj (fue el `BacktestLab`), pero corre sobre el mismo mecanismo:
`ResearchLab` → `CandidatePipeline` → `BacktestLab`, mismo proceso, mismo event
loop, mismo reloj inyectable. Con el global anterior a ADR-091, activar
`auto_cycle` habria pasado la exposicion a esa fuga de "una vez, manual, con
alguien mirando" a "una vez al dia, a las 02:00 UTC, sin nadie delante". Es
defendible ahora porque ADR-091 elimina la fuga, ADR-093 la haria visible en
media hora y ADR-095 impide arrancar contaminado — de ahi el orden 11 antes que
10.

**Punto 2 — plan de activacion en tres fases** (`docs/research.md`): A (1
simbolo × 6 genomas, ventana 02:00-03:00, 7 dias), B (2 × 12, ventana completa,
7 dias), C (pleno). Lo que se busca en la Fase A no es que el research produzca
algo util, sino **medir el consumo real** — el dato que falta desde el Bloque 6,
donde el analisis es una estimacion de orden de magnitud, no una medicion.

**Criterio de aceptacion: rollback automatico** (`app/research/rollback.py`,
ADR-096). Cuatro disparadores: CPU sostenida (>85% en 3 muestras — un pico
durante un ciclo de research es lo esperado, disparar con el primero apagaria la
vigilancia en el primer ciclo que hiciera su trabajo), latencia del bucle de
gestion (>2× su propia referencia), desviacion del reloj (>5s) y alarmas de
pipeline activas.

**Hubo que instrumentar algo que no se media.** La latencia del bucle de gestion
de posiciones — el unico que no puede llegar tarde — no tenia ninguna metrica.
Sin ella no habia forma de saber si algo le estaba robando CPU.
`ExecutionEngine.manage_latency` (ultima + EMA) es nuevo.

**Asimetria deliberada.** El rollback solo apaga el laboratorio: nunca toca la
operativa, no cierra posiciones y no puede habilitar live. Ante la duda se
sacrifica el research, que cuesta un ciclo de generacion y no dinero. **No se
rearma solo**: reactivar es decision humana, porque un rollback reversible
automaticamente convierte un problema persistente en un ciclo de
encendido/apagado, mas dificil de diagnosticar que el fallo.

**Punto 3 respetado: no se activa nada.** `auto_cycle` sigue en `false`, con
test. La vigilancia de rollback si viene activada por defecto — al reves que el
ciclo: una salvaguarda no deberia requerir que la enciendan.

**Punto 4 intacto.** `PromotionManager` fail-closed sin tocar: ninguna
estrategia generada opera contra capital sin aprobacion humana, por muchas fases
que se completen.

**Detalle de calidad.** Hay un test que exige que los umbrales documentados en
el plan sean los configurados en el codigo: un plan que diverge del codigo no
vale nada.

**Tests.** 20 nuevos. Suite: **928 en verde**; `ruff` + `black` + `mypy --strict`
limpios. ADR-096 y plan completo en `docs/research.md`.

**No se toco `qevps`.**

## 2026-08-04 — Bloque 12: criterios de graduacion a live, y el gap real medido

**Categoria:** feature+hallazgo · **Tags:** `live` `criterios` `journal` `paper` `bloque-12`

**Punto 1 — criterios formalizados** en `app/production/live/graduation.py`
(ADR-097): muestra >=400, expectativa >=+0.10R, profit factor >=1.30, drawdown
<=15%, >=60 dias en paper, >=3 regimenes con >=30 operaciones cada uno, y un
septimo que **no estaba en el enunciado**: salidas forzadas <=50%.

**Punto 2 — el gap, medido contra el journal REAL de produccion.** Traido de
`qevps` por SSH (solo lectura, con tu autorizacion explicita): 1209 operaciones,
2026-07-23 → 2026-08-04.

| Criterio | Objetivo | Real (todo) | Real (post-Bloque-1, 08-04) |
| --- | --- | --- | --- |
| Muestra | >=400 | 1209 ✅ | 134 ❌ |
| Expectativa | >=+0.10R | **-0.078R** ❌ | **-0.101R** ❌ |
| Profit factor | >=1.30 | **0.75** ❌ | **0.65** ❌ |
| Drawdown | <=15% | 27.6% ❌ | 4.6% ✅ |
| Dias en paper | >=60 | 11.8 ❌ | 0.4 ❌ |
| Regimenes | >=3 | 4 ✅ | 3 ✅ |
| Salidas forzadas | <=50% | **75.6%** ❌ | **81.3%** ❌ |

**El hallazgo, sin adornos: la expectativa es negativa en TODAS las eras.**
-0.140R (pre_contract_size), -0.097R (pre_trailing), -0.062R (post_fixes),
-0.101R (08-04). PnL acumulado **-137.59** con drawdown maximo de 138.10 — el
equity nunca supero su punto de partida. No es que falte muestra: es que **hoy
no hay edge que graduar**. El gap no se cierra esperando.

**Aviso sobre los costes.** Las comisiones registradas suman **0.00** en las
1209 operaciones. El resultado real esta por tanto *sobrestimado*: en real hay
comision y swap. La expectativa verdadera es peor que la medida.

**Dato que afecta al Bloque 7.1 (falsacion del Bloque 1).** Comparando lo
anterior al 04/08 con el dia del despliegue:

| | antes 08-04 (n=1075) | 08-04 (n=134) |
| --- | --- | --- |
| `take_profit` | 4.2% | 5.2% |
| `regime_change` | 66.7% | **72.4%** |
| Duracion mediana | 411s | **773s** |
| Expectativa | -0.075R | **-0.101R** |

El **mecanismo del Bloque 1 funciona**: la duracion mediana casi se dobla, que
es exactamente lo que el cambio pretendia. Pero las otras dos predicciones no
acompanan — `regime_change` **subio** en vez de bajar, y la expectativa empeoro.

**No declaro falsado el Bloque 1**: son 134 operaciones de un solo dia, la
ventana de falsacion (48h) no ha cerrado, y un dia de mercado puede explicarlo.
Pero la lectura preliminar **no muestra la mejora predicha**, y conviene saberlo
antes de que el veredicto automatico llegue. La hipotesis de fondo ("cortamos
las tesis antes de tiempo") puede necesitar revision: con el 72% de las salidas
decididas por regimen, alargar el holding no basta si el filtro de regimen sigue
cerrando igual.

**Churn del oro (7.2) sigue pendiente, y ahora se por que:** el oro **no opero
ni una vez el 04/08** (194 operaciones antes, 0 despues). No hay dato
post-cambio que medir. No es olvido ni falta de instrumento.

**Punto 3 — no activa nada.** El modulo no importa el `LiveGate`, no toca
`allow_live` y no tiene camino a `resolved_mode()`. Hay un test que construye un
historial que cumple **los siete** criterios y comprueba que despues
`resolved_mode()` sigue en `paper` y `allow_live` en `False`. El propio informe
lo dice por escrito: un informe que parece una aprobacion acabaria usandose como
tal.

**Punto 4 — ADR-097** con los umbrales y el razonamiento de cada uno.

**Tests.** 14 nuevos. Suite: **942 en verde**; `ruff` + `black` + `mypy --strict`
limpios. Herramienta: `python scripts/graduation_gap.py <journal.jsonl>`.

**El journal de produccion NO se copio al repo** (esta en el scratchpad de la
sesion): son datos de operativa, no codigo.

## 2026-08-04 — Bloque 13: hitos de capital derivados, no afirmados

**Categoria:** docs+feature · **Tags:** `sizing` `capital` `universo` `bloque-13`

**Diagnostico.** El Bloque 7.3 ya documento los hitos (~$1k / ~$2-5k / ~$20k) y
el punto de fondo (el tope alto expresa **granularidad del broker**, no apetito
de riesgo). Lo que faltaba, y es lo que pedia este bloque, es la **formula**:
sin ella los umbrales son numeros afirmados y hay que reinvestigarlos cada vez
que cambie el broker, el simbolo o el precio.

**Punto 1 — la formula.**

```
nocional_lote_minimo = volume_min x contract_size x precio
equity necesario para un tope = nocional_lote_minimo / (tope / 100)
```

El numerador lo fijan el broker y el mercado; el denominador crece con la
cuenta. **El tope no deja de ser necesario poco a poco: deja de serlo en un
punto concreto y calculable.**

La derivacion reproduce los hitos que estaban afirmados. El "~$20k para el oro"
no era intuicion: es $4.083 / 0.20 = **$20.414**.

| Simbolo | Nocional lote min. | @$200 | @$1k | @$5k | @$20k |
| --- | --- | --- | --- | --- | --- |
| ETHUSDm | ~$195 | 97% | 19% | 4% | 1% |
| USTECm | ~$281 | 140% | 28% | 6% | 1% |
| BTCUSDm | ~$650 | 325% | 65% | 13% | 3% |
| XAUUSDm | ~$4.083 | 2041% | 408% | 82% | **20%** |

**Punto 3 — checklist por hito** en `docs/architecture.md`, accionable linea a
linea, para no repetir la auditoria manual del 23/07 y del 27/07. Incluye un
detalle que no estaba: `max_correlation_exposure_pct` (800%) con equity pequeno
**nunca se ha activado de verdad**, asi que su grupo de correlacion cripto
conviene revisarlo *antes* de que el limite empiece a morder, no despues.

**La tabla es verificable, no prosa.** 11 tests derivan los hitos desde
`InstrumentSpec`: cuando cambie el broker, el `contract_size` o el precio de
referencia, la tabla documentada y los numeros divergiran y alguien se enterara.
Mismo criterio que en los Bloques 10 y 12.

**Punto 2 — universo de simbolos.** Hoy son **4 simbolos, y no por eleccion**:
Exness demo deshabilita las altcoins (`trade_mode=0`), descarta `BTCUSDTm` y
`USTEC_x100m` sin cotizacion y `ETHBTCm` por `contract_size=100`. De los 4, el
oro esta apagado. **Operativa real: 3 simbolos, dos de ellos altamente
correlacionados (BTC y ETH)** — por eso `max_correlation_exposure_pct` es casi
decorativo: el universo entero esta correlacionado.

Con feed/ejecucion nativa de exchange (enlaza con el Bloque 9): diversificacion
real con altcoins de regimen distinto al de BTC, y granularidad ordenes de
magnitud mejor — el problema de este bloque entero **practicamente desaparece**.
El oro no aplica: no hay XAU en un exchange cripto.

**No es una recomendacion de cambiar de broker** — es decision de capital, y
seria prematura mientras la expectativa siga negativa (ADR-097). Es el dato para
cuando esa decision se plantee: **el techo de diversificacion actual no es del
motor, es del broker.**

**Tests.** 11 nuevos. Suite: **953 en verde**; `ruff` + `black` + `mypy --strict`
limpios. Sin ningun cambio operativo aplicado: no se toco ningun tope, ni el
toggle del oro, ni el broker.

## 2026-08-04 — Bloque 9: order flow nativo — informe (y una correccion del diagnostico)

**Categoria:** hallazgo+docs · **Tags:** `order-flow` `microestructura` `mt5` `informe` `bloque-9`

**El diagnostico del enunciado era optimista, y hay que corregirlo.** La premisa
era que el order flow corre sobre el feed de MT5 y por eso sale "aproximado". La
auditoria del codigo dice algo mas fuerte: **con MT5 no esta aproximado, esta
ausente.**

- `_CAPABILITIES` del proveedor MT5 = `{TICKER, TRADES, CANDLES}` — sin
  `ORDERBOOK`. Y pese a declarar `TRADES`, `_poll_once` solo emite `Ticker`,
  nunca `Trade`: `get_recent_trades()` devuelve vacio para todo simbolo MT5.
- Efecto: `imbalance`, `book_pressure`, `spoofing_score`, `iceberg_score` y
  `consumption` son `None`/`0`; `delta`, `CVD`, `aggression`, `absorption` y
  `exhaustion` se calculan sobre lista vacia.
- El `volume` de las velas MT5 es `tick_volume` (cambios de precio, no tamano
  negociado) y `trades` reutiliza ese mismo numero.

**Confirmacion empirica en el journal real:** de 1209 operaciones, **cero** de
`delta`, `cvd` u `order_book_imbalance`. Las tres estrategias puras de order
flow nunca han disparado en produccion. Las que si operan son SMC estructural,
que se calcula sobre OHLC y no depende del libro. Diagnostico afinado: **SMC
estructural esta bien servido por MT5; el order flow no esta servido.**

**Punto 2 — desincronizacion medida, no estimada.** Compare el precio de entrada
de cada operacion contra el cierre del minuto en Binance spot:

| Par | n | Sesgo medio | Desv. mediana | p95 | Max |
| --- | --- | --- | --- | --- | --- |
| BTCUSDm vs BTCUSDT | 294 | −11.0 bps | 10.9 bps | 17.7 bps | 33.9 bps |
| ETHUSDm vs ETHUSDT | 464 | −9.8 bps | 9.8 bps | 20.2 bps | 35.4 bps |

El CFD cotiza ~10 bps por debajo del spot, de forma **estable** (la mediana
coincide con el sesgo medio: es offset, no ruido). Irrelevante para senales de
order flow, que son diferenciales; **relevante para niveles y ejecucion**, que
deben seguir usando siempre el precio del broker. El modo dual es viable solo
con esa separacion limpia.

**Punto 3 — no lo hice, y explico por que.** Comparar la tasa de falsas senales
MT5 vs libro nativo no tiene sentido: con MT5 el brazo "antes" es el conjunto
vacio. No hay 0 senales buenas contra N malas; hay **0 senales**. Lo medible
seria una evaluacion desde cero del order flow nativo, que exige el backfill de
libro pendiente desde la Fase 2 — la parte cara del bloque, y el resultado no
cambiaria la recomendacion. Averiguarlo costo menos que hacerlo.

**Punto 5 — recomendacion: no es prioritario, y no por el motivo esperado.** No
es que el dato aproximado baste. Es que **hoy no hay order flow y el motor
pierde dinero con las estrategias que si funcionan**: expectativa negativa en
todas las eras y 75% de salidas decididas por la ejecucion (ADR-097). Anadir una
familia de estrategias nueva a un motor cuya ejecucion destruye el edge de las
que ya tiene es optimizar en el orden equivocado. El order flow nativo no
arregla que el 72% de las posiciones se cierren por regimen.

**Lo que si recomiendo y es gratis:** desactivar `delta`, `cvd` y
`order_book_imbalance` mientras la fuente sea MT5, y documentar que requieren un
proveedor con libro. Una estrategia inerte que figura como activa es deuda de
honestidad, no de rendimiento. **No lo aplico**: apagar estrategias es tu
decision (misma regla que el Bloque 2).

**Cuando reabrir esto:** cuando la expectativa sea positiva y estable con las
estrategias actuales. Ademas, el mismo movimiento resolveria el techo de
diversificacion y el problema de granularidad del Bloque 13.

**Sin codigo de produccion**, como pedia el bloque: no se cambio el ruteo, no se
toco ningun proveedor, no se activo nada. Informe completo en
`docs/orderflow_nativo.md`.

## 2026-08-04 — Calibracion: volatilidad muerta, stops no adaptativos y Python 3.12

**Categoria:** fix+hallazgo · **Tags:** `volatilidad` `sizing` `calibracion` `python` `entorno`

Tres frentes pedidos tras el analisis del Bloque 12.

### 1. `volatility` era una feature muerta (ADR-098, CORREGIDO)

Llegaba `normal` en el 100 % de las 1209 operaciones. No estaba rota: estaba
calibrada para otra escala temporal. El ATR% en 1m tiene maximo observado
**0.261 %** y el umbral HIGH valia **0.80 %** — inalcanzable por construccion. El
otro lado lo cerraba el `.env` de `qevps` (`ATR_PCT_LOW=0.02`, por debajo del p5
real). La banda capturaba todo.

Importa mas de lo que parece: una constante no es un dato neutro. El
`ConfidenceEngine` la pondera, los filtros la consultan y el ML la recibe como
columna de varianza cero, donde **diluye** el peso de las que si informan.

Corregido con umbrales derivados de la distribucion real (p≈25 y p≈85) y **por
simbolo**, porque la escala no es comparable entre activos (mediana 0.038 % en
oro vs 0.068 % en ETH): un umbral unico cambiaria una constante inutil por otra.
Resolucion en escalones simbolo → global, mismo patron que ADR-083.

De paso: **`quant.context.atr_pct_high` no estaba en la whitelist** del Config
Center. Se podia ajustar en caliente el umbral bajo y no el alto, que era justo
el mal calibrado.

### 2. El R:R — recalibrar NO arregla la expectativa (medido, NO aplicado)

`atr_stop_multiplier=1.5` **no se aplica nunca**: el stop sale siempre de uno de
los dos pisos (`min_stop_pct` o `spread x8`). Por eso el stop no es adaptativo y
el objetivo acaba a 4-8x ATR, cuando el precio en 1m recorre 1-3x ATR antes de
que la operacion termine. De ahi 52 take-profits en 1209 operaciones.

ETH es caso aparte: su spread es el **78 % de su ATR**. A esa relacion
coste/movimiento no es viable para scalping, se calibre como se calibre.

**Validado en el laboratorio** (`scripts/calibrate_stops.py`, QuantCore real
sobre velas 1m reales de Binance):

- Ninguna combinacion da expectativa positiva; la mejor (R:R 1.2) pasa de
  −0.083R a −0.049R en ETH — mejora sin cruzar cero.
- En ETH, bajar `min_stop_pct` de 0.15 a 0.03 **no cambia nada** (120 ops,
  48.3 % WR, PF 0.72 en las cuatro filas): confirma que manda el piso de spread.
- **Decisivo: tambien pierde a spread CERO** (PF 0.11-0.56 en todas las
  combinaciones y ambos simbolos).

**El problema no es el coste ni donde estan los niveles: son las senales de
entrada.** Por eso **no se cambio ningun parametro de sizing** — seria mover
numeros sin evidencia. Coherente con ADR-097 (las operaciones que resuelven su
tesis dan −0.252R) y explica que el Bloque 1 no mejorase la expectativa.

Deuda anotada: `atr_stop_multiplier` es codigo muerto. O se le da efecto bajando
los pisos (con el spread barriendo el stop como contrapartida) o se elimina,
para que la config no prometa una adaptatividad que no existe.

### 3. Deriva de Python, cerrada

Instalado Python 3.12.10 junto al 3.14, con venv `.venv312` que replica
produccion (redis 5.3.1). **La suite completa pasa en el interprete que opera**,
no solo en el de desarrollo. `mypy --python-version 3.12` y `ruff
--target-version py312` tambien limpios.

Sigue pendiente decidir si el desarrollo se hace por defecto sobre 3.12 (lo
recomendable) o si se sube produccion.

## 2026-08-04 — El edge por estrategia no se replica entre simbolos

**Categoria:** hallazgo · **Tags:** `edge` `estrategias` `backtesting` `ruido` `msm`

**Pregunta que faltaba responder.** Todo el analisis previo medio el portfolio
agregado. Eso no distingue *todas pierden un poco* de *unas tapan a las que
ganan* — situaciones que llevan a decisiones opuestas.

**Metodo.** `scripts/strategy_edge.py`: cada estrategia SOLA (las otras 19
desactivadas) por el QuantCore real, sobre 10.000 velas 1m reales de Binance
(~7 dias), BTC y ETH por separado. 80 backtests.

**Resultado.** De 14 estrategias con >=10 operaciones en ambos simbolos:
**cero positivas en ambos**. Cinco cambian de signo. **Correlacion de la
expectativa entre BTC y ETH: r = +0.084.**

Los extremos lo dicen mejor que el promedio: `mss` da **+0.106R en ETH y
-0.812R en BTC**. `vwap_breakout` es la mejor de BTC (+0.211R) y negativa en ETH.

**La respuesta a la pregunta es la peor de las tres posibles:** no es que todas
pierdan un poco, ni que unas tapen a otras. Es que **el ranking entre ellas es
ruido**. No hay nada estable que podar ni que conservar.

**Lo que esto invalida, y hay que decirlo:**

- **Podar el catalogo no funcionaria**: elegir las ganadoras de un simbolo da
  las perdedoras del otro.
- **La ponderacion dinamica del MSM esta ajustando ruido.** Reponderar por
  rendimiento reciente presupone que persiste; con r=0.084 entre dos activos
  correlacionados en el mismo timeframe, no persiste. El MSM no esta mejorando
  el consenso: le mete varianza. Esto cuestiona el Bloque 3 — que hizo bien en
  arreglar el cableado, pero el gobierno que ahora si se aplica se apoya en una
  senal que no es estable.
- **Entrenar el ML sobre esto seria ajustar ruido con mas parametros.**

**Tercera confirmacion independiente del Bloque 9:** `cvd`,
`delta_confirmation` y `orderbook_imbalance` dieron **0 operaciones** en ambos
simbolos, igual que en produccion.

**Matiz registrado:** varias filas tienen expectativa en R positiva y retorno en
dinero negativo (`vwap_breakout` +0.211R con -0.01 %). La R positiva no llega a
convertirse en dinero.

**Limites, explicitos.** 7 dias, 2 simbolos, un periodo, spot de Binance y no el
CFD que opera; muestras de 10-72 operaciones por estrategia. Esto **no** prueba
que las estrategias sean irreparables: prueba que con los datos disponibles no
hay evidencia de edge en ninguna ni de un ranking estable. Refutarlo requiere un
walk-forward con mas historia y mas simbolos — que el laboratorio ya soporta y
es el siguiente paso natural.


## 2026-08-04 - Multi-timeframe: el motor solo ve 50 minutos (y el regimen casi no se usa)

**Categoria:** hallazgo - **Tags:** `multi-timeframe` `regimen` `backtesting` `fidelidad`

**Pregunta.** Toma el bot contexto de marcos superiores (1h-4h de sesgo, 15m de
direccion, 1m de entrada)? **No. Es estrictamente monotimeframe.**

Las 20 estrategias, el detector de regimen, el Market Context y el Feature Store
leen todos 1m. Con el `lookback` de 50 del detector, **toda la vision de mercado
del motor son 50 minutos**. Los unicos sitios con 5m/15m/1h/4h son los
normalizadores y proveedores; nada en el camino de decision los pide. El
agregador construye velas de 5m que nadie consume.

**Por que importa:** media biblioteca son conceptos de estructura de mercado
(`bos`, `choch`, `mss`, `order_block`, `fair_value_gap`), y la estructura leida
en 1m se rompe cada pocos minutos. Detectar estructura ahi es detectar ruido -
la mejor explicacion que tenemos para r = +0.084 entre BTC y ETH.

**Lo construido.** `app/backtesting/htf.py`: agregacion de 1m a marcos
superiores **sin lookahead** - una vela de 1h solo se publica cuando cierra,
porque publicar la que esta en curso le daria a la estrategia el maximo y el
minimo de minutos que aun no han ocurrido. Un backtest con lookahead no es
optimista, es invalido. 9 tests lo fijan, incluido el del caso con numeros.

**El experimento no resuelve la hipotesis, y hay que decirlo.** El barrido del
timeframe del regimen (1m a 1h, entrada siempre en 1m) da **seis escenarios
practicamente identicos**. No refuta nada: revela que **el regimen apenas
alimenta la decision**. Auditado: la cadena de filtros no lo consulta, el
consenso en uso (`weighted_average`) no aplica `regime_multipliers`, y de las 20
estrategias solo `mean_reversion` lo lee. Cambiar el marco de una senal que casi
nadie escucha no puede cambiar el resultado.

**La hipotesis multi-timeframe queda SIN PROBAR, no descartada.**

**Hallazgo colateral, y es el mas serio.** El backtest construye el
`ExecutionEngine` con `context=None`, asi que `regime` es siempre `"unknown"` y
`volatility` siempre `"normal"`. **La salida por cambio de regimen -el 72 % de
los cierres en produccion- no existe en el backtest.**

Corta en las dos direcciones y ambas importan:
- **Refuerza** el "no hay edge": el backtest mide las senales con salidas
  limpias de SL/TP, sin interferencia del regimen -la prueba mas favorable
  posible- y aun asi pierden a spread cero.
- **Debilita** cualquier lectura de mezcla de salidas o duracion en backtest:
  ahi backtest y produccion son sistemas distintos.

Es deuda de fidelidad del laboratorio, anterior a esta sesion, pero conviene
tenerla escrita antes de seguir usando el backtest para decidir.

**Siguiente paso real:** para probar la hipotesis no basta mover el timeframe;
hay que **dar efecto al regimen en la decision** - un filtro de sesgo que impida
abrir contra la estructura del marco superior. Es funcionalidad nueva, no un
barrido de configuracion, y por eso no la he metido sin consultar.

**Tests.** 9 nuevos. Suite: **978 en verde** en 3.14 y en 3.12.10.


## 2026-08-04 - El fix del contract_size estaba a medias: el PnL del oro salia 100x menor

**Categoria:** fix · **Tags:** `oro` `contract_size` `pnl` `riesgo` `latente`

**Como aparecio.** Preguntando por que el oro habia podido operar con 200-500 $
de balance si su lote minimo son ~4.078 $ de nocional. La respuesta: **no
cabia**. Antes del fix del 27/07 el motor calculaba el riesgo como si 0.01 lotes
fueran 0.01 onzas en vez de 1, asi que creia arriesgar 0.06 $ cuando arriesgaba
6 $ — el 3 % de la cuenta. Tomaba 100x el riesgo que creia tomar.

**Y tirando del hilo, el fix del 27/07 estaba incompleto.** Se arreglo el
sizing, pero el calculo de dinero siguio usando `quantity` (lotes):

- `Position.unrealized_pnl`, `notional`, `cost_basis`
- `PositionManager.close` (PnL realizado) y `open_risk`
- `Position.initial_risk`
- la comision del paper engine

Latente 8 dias porque en BTC/ETH/USTEC `contract_size=1` y ahi lotes == unidades:
sale bien por casualidad. Solo se manifiesta en oro, apagado desde ese mismo dia.

**Lo grave no era el PnL mal escrito**: `initial_risk` y `open_risk` alimentan el
freno de perdida diaria y el riesgo por operacion. Con el oro activo, las
perdidas se contarian 100x mas pequenas y **ninguna salvaguarda lo veria venir**.
Es decir, el bug habria mordido exactamente al reactivar el oro.

**Arreglo (ADR-099).** `Position.contract_size` + propiedad `units`; todo el
dinero pasa por `units`. El `contract_size` viaja por `OrderRequest`, se
persiste en el snapshot de recuperacion —si no, una posicion de oro restaurada
volveria a perderlo, igual que paso con `strategy`— y queda registrado en el
`TradeRecord` para poder reinterpretar operaciones antiguas.

**Tests.** 13 nuevos, incluidos los de que `contract_size=1` no cambia nada (es
donde el bug estaba escondido) y los de compatibilidad con journal y snapshots
anteriores. Suite: **991 en verde** en 3.14 y en 3.12.10. Arranque real del
motor verificado: healthy, parada limpia, guard anti-live intacto.

**No se reescribe el historial.** Las 194 operaciones de oro del 23-27/07 siguen
con el PnL mal escrito: pertenecen a la era `pre_contract_size`, ya excluida del
entrenamiento con peso 0.0.


## 2026-08-04 - Bloque 1 (Edge Intelligence): medir si el edge SIGUE ahi

**Categoria:** feat · **Tags:** `edge` `decay` `half-life` `meta-strategy` `bloque-1`

**El hueco.** Teniamos dos fuentes que responden lo mismo — el Trade Journal
(lo ejecutado) y el evaluador continuo (la senal en si) — y ninguna que responda
*sigue ganando lo mismo que ganaba*. Las dos agregan sin olvidar. Con cientos de
operaciones acumuladas, una estrategia que dejo de funcionar hace tres semanas
sigue mostrando un profit factor decente durante mucho tiempo: el peso muerto del
historial tapa el deterioro justo cuando hay que verlo.

**Que se implemento.** `app/engine/edge_research/`: ventana rodante por
estrategia (300 resoluciones), troceada en bloques, con las diez metricas del
bloque — edge decay, half-life, stability score, edge persistence, PF /
expectancy / Sharpe / Sortino / drawdown rodantes y confidence drift. Ciclo cada
15 min como `Service`, informe append-only en `data/performance/edge_reports.jsonl`,
eventos `EdgeReportGenerated` / `EdgeDecayDetected`, endpoints `/api/edge/*` e
integracion con el Meta Strategy Manager.

**Las cuatro decisiones que importan (ADR-100).**
- **`None` no es 0.0.** Toda metrica no medible llega como `None` hasta el JSON.
  Colapsarla a cero es como se acaba penalizando a una estrategia por no tener
  datos — el error contrario al que se quiere evitar.
- **`degrading` exige dos motivos concurrentes.** Con muestras de decenas de
  resoluciones, una metrica sola fuera de rango es ruido. Un motivo = `watch`.
- **La alarma se emite en la transicion, no en cada ciclo.** Reanunciar el mismo
  deterioro cada 15 minutos es como se dejan de leer las alarmas.
- **Solo frena, nunca empuja.** El multiplicador que consume el MSM vive en
  `[0.5, 1.0]`: amortigua el peso objetivo, nunca lo sube, y **nunca desactiva**
  — apagar sigue exigiendo muestra ejecutada. Sin muestra suficiente vale
  exactamente 1.0: sin evidencia no se penaliza, o toda estrategia recien
  promovida quedaria apagada antes de demostrar nada.

**Half-life es lineal a proposito**, y Sharpe/Sortino no se anualizan: ajustar
una exponencial o anualizar sobre esta muestra seria darle a la cifra una
precision que no tiene. Son alarmas de orden de magnitud.

**Efecto lateral util:** `VirtualOutcome` gana `confidence`. Sin ella no se
distingue una estrategia que empeora de una que ademas se cree cada vez mas
segura, que es la sobreconfianza y la entrada natural del Bloque 10. Las filas
anteriores quedan con `None` y fuera de esa metrica, no rellenadas.

**Archivos nuevos.** `app/engine/edge_research/{__init__,metrics,models,history,engine}.py`,
`app/dashboard/api/routes/edge.py`, `tests/unit/test_edge_research.py`,
`tests/integration/test_edge_research_integration.py`.

**Archivos modificados.** `app/config/settings.py` (`QuantEdgeResearchSettings`),
`app/engine/events/{events,__init__}.py`, `app/engine/evaluation/{outcomes,tracker}.py`
(confianza en el resultado virtual), `app/engine/{bootstrap,engine}.py` (DI y
orden de arranque: despues del evaluador, porque mide lo que ese escribe),
`app/dashboard/api/main.py`, `app/ml/{api,services/strategy_intelligence,services/__init__,meta/manager}.py`.

**Riesgos conocidos.** La ingesta relee el JSONL entero cada ciclo y deduplica
por `signal_id` con ventana acotada (`seen_limit=50k`): coste lineal con el
tamano del fichero, y si el store superara esa ventana la deduplicacion dejaria
de ser total. Aceptable a esta cadencia y acotado por configuracion, pero es lo
primero a revisar si el store crece de orden.

**TODOs restantes.** El resumen 0-100 mezcla cuatro dimensiones con pesos que son
un juicio; quedan configurables y el informe conserva siempre las metricas
individuales. Falta panel de dashboard (hoy solo API). Bloques 2-15 pendientes.

**Tests.** 31 nuevos (23 unitarios + 8 de integracion). Suite: **1022 en verde**
en 3.14 y en 3.12.10. Ruff, Black y MyPy strict limpios.


## 2026-08-04 - Bloque 2: explicar una operacion sin inventarse la causa

**Categoria:** feat · **Tags:** `atribucion` `factores` `lift` `bloque-2`

**El riesgo del bloque no era tecnico, era epistemico.** "Explicar por que gano
o perdio cada operacion" invita a producir una descomposicion aditiva del
resultado por factor. Seria falso: los factores estan correlacionados entre si
y una sola operacion no contiene evidencia causal de nada. Un informe asi suena
convincente **siempre**, incluso cuando no sabe nada, que es exactamente lo
peor que puede hacer una herramienta de decision.

**Lo que se implemento en su lugar.** `app/engine/attribution/`: asociacion
historica por bucket. Cada factor se trocea en terciles (continuos) o por
etiqueta (categoricos), se mide la R media de cada bucket contra la global, y
ese `lift` es lo que se atribuye. Explicar una operacion = decir en que bucket
cayo y cuanto ha rendido historicamente ese bucket.

**Cuatro decisiones de honestidad (ADR-101).**
- **El residuo se reporta siempre**: `R - baseline - suma(lifts)`. Un residuo
  grande dice que la explicacion no explica, y eso es informacion.
- **El aviso viaja en el JSON** (`caveat`), no solo en la doc: el informe se lee
  en un dashboard, fuera de contexto.
- **Buckets pequenos descartados**: una etiqueta con dos operaciones da un lift
  enorme y sin significado, y en un ranking por magnitud sube arriba del todo
  justo por ser ruido.
- **`join_breakdown` separa los dos huecos**: "sin decision_id" (trade adoptado)
  y "sin foto" (decision anterior al bloque). Agregarlos escondería cual duele.

**Donde se captura.** Desde el Event Bus (`DecisionGenerated`), no dentro del
Decision Engine: el camino caliente no puede pagar lecturas extra del Feature
Store por evaluacion. El precio —desfase de hasta el TTL del store— se **mide**
en `lag_seconds` de cada fila en vez de fingir simultaneidad.

**Hueco encontrado y cerrado durante los tests.** Con `persist_snapshots=false`
el store no retenia nada, asi que la atribucion salia vacia sin decir por que:
un ajuste de I/O apagaba en silencio una funcionalidad entera. Ahora retiene
siempre una ventana en memoria.

**Archivos nuevos.** `app/engine/attribution/{__init__,models,store,capture,engine}.py`,
`app/dashboard/api/routes/attribution.py`, `tests/unit/test_edge_attribution.py`,
`tests/integration/test_edge_attribution_integration.py`.

**Archivos modificados.** `app/config/settings.py` (`QuantAttributionSettings`),
`app/engine/events/{events,__init__}.py` (`AttributionReportGenerated`),
`app/engine/{bootstrap,engine}.py`, `app/dashboard/api/main.py`.

**Riesgos conocidos.** El buffer de decisiones es acotado: si una decision rota
antes de llegar su evento, la foto se toma igual pero sin desglose de confianza
ni contexto (quedan `None`/`unknown`). Los terciles se recalculan cada ciclo, asi
que los cortes se mueven con la muestra; se guardan junto al agregado para que
explicar use exactamente los cortes con los que se midio.

**TODOs restantes.** El factor `ml` queda declarado pero sin proveedor cableado
(el modelo activo no expone prediccion por simbolo fuera del pipeline de
inferencia): se observa como `None`, que es distinto de observarlo en cero.

**Tests.** 24 nuevos (17 unitarios + 7 integracion). Suite: **1046 en verde**.
Ruff, Black y MyPy strict limpios. Cableado DI verificado contra
`build_container` real.


## 2026-08-04 - Bloque 3: microestructura completa sobre datos que hoy no llegan

**Categoria:** feat · **Tags:** `microestructura` `libro` `order-flow` `bloque-3`

**El hecho que gobierna el bloque.** Las nueve metricas pedidas (queue
imbalance, queue estimation, arrival rate, cancel rate, resiliency,
replenishment, liquidity consumption, market impact, execution pressure) salen
todas del libro de ordenes incremental. Y con MT5 —el broker de la demo— no hay
libro: ya estaba medido en `docs/orderflow_nativo.md` (el proveedor no expone
`ORDERBOOK` y nunca emite `Trade`).

**Decision: implementar completo y declarar la ausencia.** El motor funciona
contra el libro que los proveedores nativos si publican (Fase 2), y cuando no
hay datos devuelve `observable=false` con su motivo y **todas las metricas en
`None`**. Fabricar un `queue_imbalance=0.0` seria indistinguible de un libro
perfectamente equilibrado y alimentaria al Decision Engine con una lectura que
nadie tomo.

**Tres detalles que separan una metrica util de una que miente.**
- **Deltas, no snapshots**: sin ver deltas y operaciones por separado, una
  cancelacion y una ejecucion son la misma resta, y `cancel_rate` mide las dos.
- **Un resync no es una avalancha de ordenes**: `is_snapshot` reinicia el estado
  en vez de contarse como altas. Si no, cada perdida de conexion dispararia el
  arrival rate justo cuando el motor se quedo ciego.
- **Impacto: `None` antes que extrapolar** mas alla del ultimo nivel publicado.

**Integracion con el Decision Engine: fail-open.** El filtro veta solo con
presion **medida** por encima del umbral; sin medicion pasa. Un filtro que
bloquea por falta de datos apagaria el motor con el broker actual sin un solo
error en el log. Y si el motor no esta cableado, el filtro ni entra en la
cadena: fuera no aparece en la explicacion de la decision, que no es lo mismo
que aparecer diciendo "pase".

**Archivos nuevos.** `app/engine/microstructure/{__init__,models,engine,features}.py`,
`app/dashboard/api/routes/microstructure.py`, `tests/unit/test_microstructure.py`,
`tests/integration/test_microstructure_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/filters/{filters,__init__}.py`
(`MicrostructureFilter`), `app/market/collector/collector.py` (Protocol
`BookObserver` + hook en el camino de deltas y trades), `app/engine/bootstrap.py`,
`app/dashboard/api/main.py`.

**Riesgo conocido y honesto.** Con MT5 el bloque entero queda **inerte**: es
codigo correcto sin datos que lo alimenten. Solo cobra valor si se decide
alimentar los indicadores desde un feed nativo — decision abierta desde el
informe de order flow, y que no tomo este bloque.

**Tests.** 25 nuevos (14 unitarios + 11 integracion). Suite: **1071 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 4: un pronostico que se puntua a si mismo

**Categoria:** feat · **Tags:** `regimen` `pronostico` `brier` `bloque-4`

**La trampa del bloque.** "Pronosticar el regimen" produce facilmente cinco
probabilidades de aspecto convincente. Detectar es observar; pronosticar es
apostar. Un motor que apuesta y no lleva marcador es una opinion con decimales.

**Metodo simple a proposito.** Frecuencia condicional empirica sobre
`regimen|volatilidad`. Nada de HMM ni Markov ajustado: con esta muestra, un
modelo mas rico da parametros peor estimados y un numero mas dificil de auditar.

**Lo que sostiene el bloque es la validacion (ADR-103).** Todo pronostico se
guarda, se resuelve contra lo que paso y se puntua con Brier multiclase **contra
el pronostico trivial**. `skill = 1 - brier/baseline`, y viaja en el evento del
bus. Si sale negativo se publica igual: es la senal de que no vale para decidir
nada, y esconderla seria peor que no tener pronostico.

**Tres decisiones de honestidad.**
- **Sin muestra no se reparte a partes iguales**: `observable=false`. Un reparto
  uniforme parece un pronostico y no lo es.
- **Laplace, no ceros**: un desenlace nunca visto con probabilidad 0 afirma que
  algo es imposible por no haberlo visto unos cientos de veces.
- **Confianza != acierto**: la confianza mide evidencia y satura con la muestra;
  el acierto lo dice el Brier. Son campos separados justamente por eso.

**Orden del ciclo:** resolver antes de emitir. Al reves, el pronostico recien
emitido entraria en su propia validacion.

**Archivos nuevos.** `app/engine/regime_forecast/{__init__,models,engine,service}.py`,
`app/dashboard/api/routes/forecast.py`, `tests/unit/test_regime_forecast.py`,
`tests/integration/test_regime_forecast_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/events/{events,__init__}.py`
(`RegimeForecastUpdated`), `app/engine/{bootstrap,engine}.py`, `app/dashboard/api/main.py`.

**Sesgo conocido del bootstrap.** Sembrar del historico atribuye a toda la serie
la condicion detectada al final, porque re-detectar hacia atras exigiria datos
que en su momento no existian. La muestra inicial es por tanto mas ruidosa que
la que el motor acumula despues en vivo. Ademas, los umbrales de clasificacion
de desenlaces (1.5x / 0.5x el rango previo) son un punto de partida razonable,
no una medida calibrada por simbolo.

**Tests.** 24 nuevos (16 unitarios + 8 integracion). Suite: **1095 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 5: la correlacion escrita a mano envejece; esta se mide

**Categoria:** feat · **Tags:** `correlacion` `lead-lag` `cointegracion` `bloque-5`

**El hueco real.** El filtro de correlacion vetaba por grupos declarados a mano
en configuracion. Un grupo escrito hace meses no sabe que dos simbolos han
dejado de moverse juntos — ni que dos que no estaban en el mismo grupo ahora se
mueven como uno solo. El filtro creia estar protegiendo de una concentracion que
ya no existia, y no veia la que si.

**Implementado.** `app/engine/correlation/`: correlacion rodante y dinamica
(EWMA), lead-lag con su convencion de signo, ratio de cobertura y vida media del
residuo, ranking de liderazgo, correlacion por sesion y matriz de pares. Servicio
con ciclo propio que mide **inmediatamente al arrancar** — si no, el filtro
pasaria el primer intervalo entero sin la evidencia que este motor existe para
darle, y nadie sabria por que.

**Lo que NO se implemento, y se dice (ADR-104).** No hay test ADF de
cointegracion. Necesita tablas de valores criticos, y fingir un p-valor seria
peor que no darlo. Se reporta vida media del residuo: si revierte rapido, el par
se comporta como cointegrado; si no revierte, `None`. Es lo accionable y se
llama por su nombre.

**Bug encontrado por un test, no en produccion.** El lead-lag reportaba
liderazgo inventado entre series **simultaneas**: una serie periodica
correlaciona igual de bien consigo misma a lag 0 y a un multiplo de su periodo,
y sin regla de desempate ganaba el que saliera antes en la iteracion. Ahora los
empates los gana el desplazamiento menor: ante la misma evidencia, la
explicacion mas simple es que se mueven a la vez.

**Integracion con el filtro: suma, no sustituye.** Los grupos manuales siguen
siendo regla dura. Sin motor cableado el filtro se comporta exactamente como
antes del bloque, y hay un test que lo fija.

**Archivos nuevos.** `app/engine/correlation/{__init__,stats,engine}.py`,
`app/dashboard/api/routes/correlation.py`, `tests/unit/test_correlation.py`,
`tests/integration/test_correlation_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/filters/filters.py`
(`CorrelationFilter` acepta correlacion medida), `app/engine/{bootstrap,engine}.py`,
`app/dashboard/api/main.py`.

**Riesgos conocidos.** La correlacion por sesion trocea la misma ventana: con
pocas velas por sesion, esa sesion no aparece (correcto, pero puede leerse como
que solo existen las sesiones activas). `min_correlation` es un umbral afirmado,
no derivado de la distribucion real de correlaciones del universo.

**Tests.** 22 nuevos (16 unitarios + 6 integracion). Suite: **1117 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 6: comparar IOC/LIMIT/MARKET sin que gane el que no opera

**Categoria:** feat · **Tags:** `ejecucion` `optimizador` `slippage` `bloque-6`

**La trampa del bloque.** Si se suman solo los costes de ejecutar, LIMIT gana
siempre: no paga spread —lo cobra— y no sufre slippage. Un optimizador asi es
una maquina de no operar, y ademas lo parece hacer bien.

**La pieza que lo arregla: el coste de NO ejecutar.**
`coste_esperado = directo x P(llenado) + (1-P) x coste_de_fallar`, con el coste
de fallar escalado por una `urgency` 0-1. Se declara como lo que es: **una
politica, no una medida**. Nadie ha medido cuanto vale la operacion que no se
abre; el parametro hace explicita la decision en vez de esconderla.

**Error de modelado que se detecto con un test.** La primera version le daba al
IOC el spread a favor (como pasivo) **y** alta probabilidad de llenado (como
agresivo). Con las dos ventajas ganaba siempre — por contabilidad, no por
merito. Un optimizador que elige por un error de contabilidad es peor que no
tener optimizador. Ahora IOC cruza el spread y sufre slippage igual que MARKET;
lo que lo distingue es que puede quedarse a medias.

**Reutiliza los motores de la Fase 5** (slippage, latencia) en vez de duplicar
el modelo: si optimizar y simular usaran modelos distintos, el optimizador
estaria eligiendo para un mercado que el simulador no vive, y la discrepancia
solo se veria en live.

**Sin libro no se inventa la probabilidad de llenado.** El desequilibrio de cola
(Bloque 3) corrige la base solo si es observable. Con MT5 se queda en la base:
es el escenario en el que un optimizador confiado prefiere limites que nunca se
llenan.

**Limite explicito del bloque.** El optimizador **cotiza pero no rutea**: el
Execution Engine sigue mandando MARKET. Cablear la eleccion al envio real toca
la Fase 5 y cambia el comportamiento de ejecucion; se deja fuera a proposito y
queda escrito como pendiente.

**Archivos nuevos.** `app/execution/optimizer/{__init__,optimizer}.py`,
`app/dashboard/api/routes/optimizer.py`, `tests/unit/test_execution_optimizer.py`,
`tests/integration/test_execution_optimizer_integration.py`.

**Archivos modificados.** `app/config/settings.py` (`ExecutionOptimizerSettings`),
`app/engine/bootstrap.py`, `app/dashboard/api/main.py`.

**Tests.** 19 nuevos (15 unitarios + 4 integracion). Suite: **1136 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 7: la calidad de la posicion no es la calidad de la senal

**Categoria:** feat · **Tags:** `calidad` `veto` `filtros` `bloque-7`

**Por que es un motor aparte y no un factor mas del score.** El score mide la
oportunidad; esto mide la **posicion** que saldria de ella. Una senal excelente
en un mercado sin liquidez, con el coste de entrada comiendose media R, es una
mala posicion aunque sea una buena senal. Metido en el score se diluiria en una
media y nadie sabria que fue la calidad lo que paro la operacion.

**Se materializa como filtro** para que el veto aparezca en la explicacion de la
decision con su motivo: es la unica forma de auditar despues por que no se opero.

**Tres reglas que evitan que el veto se vuelva un apagon (ADR-106).**
- **Las dimensiones ausentes no valen cero.** Un cero dice "es malo"; la
  ausencia dice "no se sabe". Se reportan en `missing` con su motivo.
- **Fail-open por debajo del minimo de evidencia.** Con el broker actual —sin
  libro, sin coste estimado en algunos simbolos— bloquear con dos dimensiones
  observables apagaria el motor sin un solo log raro.
- **Suelos por dimension.** Promediar deja que una liquidez pesima se esconda
  detras de un setup excelente. Y cuando el suelo no veta, el aviso viaja igual.

**El coste se juzga contra la R esperada**, no contra un techo: 8 bps son
baratos para 3 R y carisimos para 0.2 R. Sin R estimada se cae al techo — peor,
pero sin inventar.

**Limite real del cableado, escrito por delante.** El lector de coste/R/riesgo
**no esta cableado**: esas magnitudes viven en la ejecucion y en el Risk
Manager, y no se conocen cuando corre la cadena de filtros. Hoy el veto opera
con la mitad de su informacion, esas dimensiones quedan no observables y no
bloquean. Hay un test que lo fija para que el dia que se cablee sea una decision
y no un accidente.

**Archivos nuevos.** `app/engine/position_quality/{__init__,engine}.py`,
`tests/unit/test_position_quality.py`,
`tests/integration/test_position_quality_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/filters/{filters,__init__}.py`
(`PositionQualityFilter`), `app/engine/bootstrap.py`.

**Tests.** 19 nuevos (14 unitarios + 5 integracion). Suite: **1155 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 8: un +3% no dice de donde salio ese +3%

**Categoria:** feat · **Tags:** `portfolio` `atribucion-pnl` `concentracion` `bloque-8`

**La pregunta que el agregado esconde.** Si el mes cierra en +3%, ¿viene de las
cuatro estrategias por igual o de una racha de un simbolo en una sesion que no
se va a repetir? Cambia por completo que hacer despues, y el numero agregado no
la contiene.

**Implementado.** `app/portfolio/intelligence.py`: fuente del PnL (bruto vs
comisiones), contribucion por simbolo, estrategia, sesion y regimen, heatmap
(simbolo x estrategia), concentracion de Herfindahl y su lectura legible en
"apuestas efectivas".

**Cuatro decisiones que evitan que el informe mienta (ADR-107).**
- **Cuotas sobre el PnL positivo, no sobre el neto.** Con ganancias y perdidas
  mezcladas la suma neta se acerca a cero y las cuotas se disparan o cambian de
  signo. Sobre el positivo, "aporto el 40% de lo que se gano" siempre significa
  lo mismo.
- **Un grupo que pierde no suma concentracion.** Elevar al cuadrado una cuota
  negativa sumaria concentracion; un grupo perdedor diluye el origen del
  beneficio, no lo concentra.
- **Cada contribucion viaja con su muestra.** "El 80% del PnL vino de X" se lee
  como merito cuando puede ser una muestra de nueve operaciones.
- **Lo no atribuido tiene grupo propio**: dice cuanta parte del PnL todavia no
  se puede explicar, en vez de repartirse o desaparecer.

**Dos avisos en la propia carga util**, no solo en la doc: esto mide PnL
realizado y no exposicion viva, y una contribucion alta puede ser racha.

**Archivos nuevos.** `app/portfolio/{__init__,intelligence}.py`,
`app/dashboard/api/routes/portfolio.py`, `tests/unit/test_portfolio_intelligence.py`,
`tests/integration/test_portfolio_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/bootstrap.py`,
`app/dashboard/api/main.py`.

**Limite declarado.** El desglose de coste solo separa comision del bruto. El
reparto fino (fees, slippage, spread, latencia, coste oculto y de oportunidad)
es el Bloque 9 y no se adelanta aqui.

**Tests.** 17 nuevos (13 unitarios + 4 integracion). Suite: **1172 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 9: el coste oculto no es un concepto, es un agujero

**Categoria:** feat · **Tags:** `costes` `slippage` `oportunidad` `bloque-9`

**La decision que hace util al bloque.** El "coste oculto" se calcula como
residuo: `bruto - neto - (comisiones + slippage + spread)`. Si crece, significa
que el sistema **no esta midiendo** algo. Es un detector de contabilidad
incompleta, y el informe alza una nota cuando pasa del umbral. Un modulo que
inventara una formula para el coste oculto perderia justo esa senal.

**Por eso la latencia no se estima.** El journal no la registra por operacion,
asi que sale como `None` —no medida— y cae dentro del residuo, con su nota. Si
se estimara, se mezclaria con el residuo y el detector dejaria de detectar.

**El coste de oportunidad se mide, no se conjetura.** El evaluador continuo ya
resuelve cada senal contra el mercado posterior: una senal con R virtual
positiva cuyo `signal_id` no aparece en ningun trade es una oportunidad perdida
**medida**. Solo cuentan las que habrian ganado — una senal no ejecutada que
habria perdido es una bala esquivada, y sumarla con signo contrario dejaria el
numero en nada.

**Y no se reparte por dia**: es un coste del conjunto, y repartirlo lo contaria
tantas veces como dias tenga el informe.

**Los bps se convierten sobre unidades, no sobre lotes** (ADR-099): en oro un
lote son 100 onzas, y medir sobre `quantity` dejaria el coste 100 veces por
debajo. Hay un test que lo fija con `contract_size=100`.

**Archivos nuevos.** `app/execution/costs/{__init__,attribution}.py`,
`app/dashboard/api/routes/costs.py`, `tests/unit/test_cost_attribution.py`,
`tests/integration/test_cost_attribution_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/bootstrap.py`,
`app/dashboard/api/main.py`.

**Riesgos conocidos.** Slippage y spread vienen como medias de la operacion: una
entrada con slippage alto y una salida limpia se promedian y se pierde el
detalle por tramo. El coste de oportunidad esta en R, no en dinero, porque las
senales no ejecutadas no tienen sizing.

**Tests.** 14 nuevos (10 unitarios + 4 integracion). Suite: **1186 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 10: la confianza que nadie habia puesto a prueba

**Categoria:** feat · **Tags:** `calibracion` `ece` `brier` `sobreconfianza` `bloque-10`

**El problema es que no duele.** Una confianza de 0.8 que en realidad acierta el
50% no produce ningun error, ningun log, ninguna alarma. Se mira junto al
resultado individual, donde no hay forma de verla fallar. Solo aparece en
agregado, y solo si alguien la mide.

**Implementado.** `app/ml/calibration/`: curva de calibracion, diagrama de
fiabilidad listo para pintar, ECE ponderado por muestra, Brier score, sesgo con
signo, sobreconfianza e infraconfianza separadas, y un factor de correccion.

**La decision que define el bloque (ADR-109): se mide y se expone, no se aplica
sola.** Una capa que corrigiera en silencio su propia entrada haria imposible
saber si el modelo mejoro o si solo se le esta tapando el error — y el proximo
que mirase el ECE lo veria bien sin que nada hubiera mejorado. La correccion
esta disponible en `MLEngine.calibration.calibrated()` y quien la consuma decide.

**Y esta acotada.** Sin techo, una racha de 60 operaciones puede dar un factor
de 0.4 que apagaria medio sistema. Es un empujon, no un volantazo.

**Sin muestra, correccion 1.0**, e informe con `observable=false` y su motivo.
Los tramos con menos de `min_bin_sample` se descartan: un bin con tres
observaciones da 0.0 o 0.67 y arrastra el ECE con ruido puro.

**Archivos nuevos.** `app/ml/calibration/{__init__,engine}.py`,
`tests/unit/test_confidence_calibration.py`,
`tests/integration/test_confidence_calibration_integration.py`.

**Archivos modificados.** `app/config/settings.py` (`MLCalibrationSettings`),
`app/ml/api.py` (`MLEngine.calibration` y `run_calibration()`),
`app/dashboard/api/routes/ml.py` (`/api/ml/calibration`).

**Riesgos conocidos.** El acierto se define como `pnl > 0`, que es lo que el
journal permite: una operacion que gana 0.1 R cuenta igual que una de 3 R, asi
que se mide direccion y no magnitud. Y la correccion es global, no por
estrategia ni por regimen: un sistema bien calibrado en tendencia y mal en rango
recibe un unico factor promedio.

**Tests.** 17 nuevos (13 unitarios + 4 integracion). Suite: **1203 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 11: la calidad del dato, medida y con consecuencias

**Categoria:** feat · **Tags:** `calidad-dato` `riesgo` `reloj` `bloque-11`

**Por que este bloque existe.** El 04/08 el motor se quedo ciego sin lanzar un
solo error: el validador descartaba el 100% de los ticks, el bucle corria, el
log no decia nada, y el sistema dejo de operar cuatro dias. Este bloque mide esa
salud y la convierte en consecuencia.

**Reducir, no apagar.** El multiplicador vive en `[0.3, 1.0]` y nunca llega a 0:
apagar por una metrica de calidad convierte un problema de datos en una parada
total, y esas las decide el kill switch, que tiene auditoria propia.

**Dos fallos de diseno que encontraron los tests, no produccion.**
1. **La media diluia lo critico.** Con `missing_data=0` o `clock_drift=0`, siete
   senales sanas mantenian el score por encima del umbral y no pasaba nada. Ahora
   las senales criticas degradan por si solas y el multiplicador toma el camino
   mas severo. `tick_quality` es critica por el motivo mas concreto posible: el
   fallo del 04/08 fue literalmente eso.
2. **El tope de exposicion se tragaba la reduccion.** Aplicado antes de los
   topes, reducir a la mitad una cantidad que el tope iba a recortar igualmente
   no reducia nada — y la proteccion desaparecia justo en las operaciones mas
   grandes. Ahora se aplica despues de los topes y antes del redondeo a lotes.

**Ausencia de medicion no es calidad cero.** Sin mensajes, `feed_quality` queda
no observable. Y sin ninguna senal observable el multiplicador se queda en 1.0:
reducir ahi seria castigar por no haber medido.

**Archivos nuevos.** `app/monitoring/{data_quality,data_quality_service}.py`,
`app/dashboard/api/routes/quality.py`, `tests/unit/test_data_quality.py`,
`tests/integration/test_data_quality_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/monitoring/events.py`
(`DataQualityDegraded`), `app/execution/sizing/engine.py` (parametro
`risk_multiplier`, retrocompatible), `app/execution/execution_engine/engine.py`
(lector del multiplicador), `app/engine/{bootstrap,engine}.py`,
`app/dashboard/api/main.py`.

**Riesgos conocidos.** Los contadores del feed son acumulados desde el arranque,
no de ventana movil: un episodio malo temprano sigue pesando horas despues. Y
`max_timestamp_drift_seconds` no se alimenta todavia (el collector no expone el
desfase por mensaje), asi que esa senal queda no observable.

**Tests.** 21 nuevos (14 unitarios + 7 integracion). Suite: **1224 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 12: la maquina tambien es un riesgo, y no se ve en el PnL

**Categoria:** feat · **Tags:** `meta-riesgo` `infraestructura` `cpu` `bloque-12`

**Lo que hace peligroso a este riesgo.** Una VPS al 95% de CPU no impide operar:
impide operar **a tiempo**. El motor sigue decidiendo, las ordenes salen, y la
degradacion aparece como slippage y salidas tardias — que se leen como mala
suerte de mercado. El dano es real y la causa es invisible desde cualquier
metrica de trading.

**Composicion por producto (ADR-111).** El multiplicador final es
`infraestructura x calidad_de_dato`. Un feed mediocre en una maquina saturada es
peor que cualquiera de las dos cosas por separado, y quedarse con el minimo lo
negaria. El suelo se aplica al final.

**Se miden en el mismo ciclo y en orden**: primero calidad, despues meta-riesgo,
que la consume. Con bucles separados, el meta-riesgo compondria con una lectura
de calidad de hasta un minuto de antiguedad.

**Fallo de diseno que encontro un test.** Con rampa lineal, una CPU al 20% frente
a un techo del 90% daba senal 0.78: la maquina sana parecia a medio gas, y como
las senales se promedian, la degradacion real no destacaba sobre el fondo. Ahora
hay **zona de confort**: por debajo de la mitad del limite, el recurso esta sano
del todo.

**Un componente que no reporta no es un componente caido.** Redis en local, MT5
en cripto: quedan no observables. Penalizarlos apagaria medio sistema por
configuracion, no por averia.

**Archivos nuevos.** `app/monitoring/meta_risk.py`, `tests/unit/test_meta_risk.py`,
`tests/integration/test_meta_risk_integration.py`.

**Archivos modificados.** `app/config/settings.py` (`MetaRiskSettings`),
`app/monitoring/data_quality_service.py` (ciclo compartido),
`app/engine/bootstrap.py` (la ejecucion pasa a leer el multiplicador COMPUESTO),
`app/dashboard/api/routes/quality.py`.

**Riesgos conocidos.** Se lee el ultimo snapshot del health monitor en vez de
forzar uno: evita meter `psutil` en el camino de cada medicion, a cambio de una
lectura de hasta un ciclo de antiguedad. Los pesos son juicios: nadie ha medido
todavia la relacion entre CPU alta y slippage real — el Bloque 15 podria darla.

**Tests.** 16 nuevos (12 unitarios + 4 integracion). Suite: **1240 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 13: importancia de features, y por que no SHAP

**Categoria:** feat · **Tags:** `ml` `features` `permutacion` `bloque-13`

**El enunciado pedia SHAP "si existe backend compatible". No lo hay, y se
explica en vez de dejarlo como hueco.** SHAP exige una dependencia binaria
pesada y esta pensado para arboles de gradiente y redes; los modelos de este
proyecto son propios y algunos backends son opcionales. Anadirla cubriria parte
del catalogo y habria que caer a otra metrica para el resto: dos numeros
distintos llamados igual, que es peor que uno bien entendido.

**Lo implementado: importancia por permutacion.** Se baraja una feature y se
mide cuanto empeora el modelo. Agnostica, aplicable a todo el catalogo, y
responde la pregunta operativa exacta: cuanto costaria perder esta feature.

**La importancia nativa del modelo viaja aparte.** Mide otra cosa —cuanto usa el
modelo una feature, no cuanto se pierde si desaparece— y promediarlas daria un
numero que no responde a ninguna de las dos.

**Tres detalles que hacen fiable la cifra (ADR-112).** Varias repeticiones (un
solo barajado hace "importante" a una feature irrelevante por azar), semilla
fija (sin ella el `change` mediria el ruido del metodo) y la historia se
actualiza **despues** de comparar (si no, la medicion entraria en su propia
referencia y el cambio saldria amortiguado).

**Archivos nuevos.** `app/ml/importance/{__init__,tracker}.py`,
`tests/unit/test_feature_importance.py`,
`tests/integration/test_feature_importance_integration.py`.

**Archivos modificados.** `app/config/settings.py` (`MLImportanceSettings`),
`app/ml/api.py` (`MLEngine.importance` y `run_importance()`),
`app/dashboard/api/routes/ml.py` (`/api/ml/importance`).

**Riesgos conocidos.** La permutacion es O(features x repeticiones x
predicciones): con muchas features y datasets grandes el ciclo es caro, por eso
se dispara bajo demanda y no en bucle. Y con features correlacionadas reparte
mal el credito: si dos llevan la misma informacion, barajar una apenas empeora
el modelo y ambas parecen poco importantes.

**Tests.** 15 nuevos (12 unitarios + 3 integracion). Suite: **1255 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 14: la explicacion del rechazo, convertida en datos

**Categoria:** feat · **Tags:** `rechazos` `explicabilidad` `filtros` `bloque-14`

**El motor ya explicaba sus rechazos. En texto.** Y el texto sirve para leer UNA
decision y para nada mas: no se puede agregar, no se puede contar, y no responde
la pregunta que importa — *que filtro me esta costando operaciones este mes*.

**Lo que NO se hizo, y por que.** El enunciado pide "penalizacion de cada
filtro". Los filtros de este sistema no restan puntos: vetan. Modelarlos como si
penalizaran produciria numeros con aspecto de calculo que no corresponden a
nada. Se registra lo que ocurre de verdad: los **umbrales** llevan valor, minimo
y deficit —esa si es una penalizacion medible—, y los **filtros** son binarios.

**Se cablea en el Decision Engine, no en el bus**, porque el resultado de cada
filtro y el deficit de cada umbral solo existen ahi: el evento de decision no
los lleva y reconstruirlos desde fuera seria adivinarlos.

**Dos decisiones sobre el volumen y el denominador.**
- **Tambien se registran las aceptadas**: sin ellas, "este filtro bloqueo 40
  veces" no significa nada.
- **Las evaluaciones sin senal se cuentan, no se guardan**: dos simbolos por
  segundo son ~170.000 filas al dia de algo que no es un rechazo, y ahogarian
  los rechazos reales.

**El resumen cuenta dos cosas distintas**: cuantas veces bloqueo cada puerta, y
cuantas veces fue **la unica**. La segunda es la que decide si relajar un filtro:
una puerta que siempre bloquea acompanada de otras no cuesta nada.

**Archivos nuevos.** `app/engine/rejections/{__init__,models,store}.py`,
`app/dashboard/api/routes/rejections.py`, `tests/unit/test_rejections.py`,
`tests/integration/test_rejections_integration.py`.

**Archivos modificados.** `app/config/settings.py`,
`app/engine/decision_engine/engine.py` (desglose estructurado),
`app/engine/bootstrap.py`, `app/dashboard/api/main.py`.

**Riesgo conocido.** El registro vive en el camino de cada decision: es un append
a memoria y un volcado por lotes, pero es trabajo en el camino caliente. Si el
volumen de decisiones creciera un orden de magnitud habria que moverlo a un
worker propio.

**Tests.** 21 nuevos (14 unitarios + 7 integracion). Suite: **1276 en verde**.
Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Bloque 15: el carril que no existe, y por que eso es el punto

**Categoria:** feat · **Tags:** `benchmark` `live` `slippage` `bloque-15`

**El problema logico del bloque, dicho por delante.** Con solo paper e ideal, el
"gap de ejecucion" es —por construccion— el slippage y el spread que el propio
simulador modelo. No mide nada independiente: es el modelo mirandose al espejo.
Publicarlo como "slippage real" seria la clase de cifra que engana precisamente
porque suena a observacion.

**Decision: implementarlo como linea base y etiquetarlo como tal (ADR-114).** El
valor del numero es futuro: el dia que exista carril live, la diferencia entre
el gap modelado y el real dira si el simulador miente y cuanto. Hasta entonces:
`fill_difference_bps` es **`None`** y no cero (cero afirmaria que paper y live
coinciden, y eso no se ha observado nunca), el carril live sale con su motivo, y
el aviso viaja en la propia carga util junto con `live_enabled: false`.

**El fill ideal no es una simulacion aparte**: es el resultado real mas los
costes que se le descontaron. Un segundo simulador introduciria una diferencia
que vendria de la discrepancia entre modelos, no de la ejecucion.

**Sin muestra, cero recomendaciones.** Una recomendacion es una llamada a la
accion; emitirla sobre diez operaciones es peor que callarse.

**La regla del proyecto intacta.** El motor no recibe proveedor de operaciones
live, no conoce ningun broker y no tiene ruta de ejecucion. El invariante se
expone en `status()` para poder verificarlo desde fuera, y un test de
integracion comprueba que el guard anti-live sigue resolviendo a `paper`.

**Archivos nuevos.** `app/execution/benchmark/{__init__,shadow}.py`,
`app/dashboard/api/routes/benchmark.py`, `tests/unit/test_shadow_benchmark.py`,
`tests/integration/test_shadow_benchmark_integration.py`.

**Archivos modificados.** `app/config/settings.py`, `app/engine/bootstrap.py`,
`app/dashboard/api/main.py`.

**Tests.** 19 nuevos (14 unitarios + 5 integracion). Suite: **1295 en verde** en
3.14 y en 3.12.10. Ruff, Black y MyPy strict limpios.


## 2026-08-05 - Cierre de la tarea Edge Intelligence (bloques 1-15)

**Categoria:** hito · **Tags:** `edge-intelligence` `cierre` `bloques-1-15`

**Los 15 bloques implementados, con tests y documentacion.** De 991 tests al
empezar a **1295**, verdes en 3.14 y en 3.12.10, con Ruff, Black y MyPy strict
limpios en cada bloque antes de pasar al siguiente.

**El hilo comun de las 15 decisiones.** Casi todos los bloques pedian producir
un numero, y en casi todos el numero facil habria sido falso. La regla que
gobierna el trabajo entero es la misma en los quince: **la ausencia de medicion
no es un cero**. Aparece con distinta cara cada vez —`observable=False` en
microestructura sin libro, `None` en la latencia no registrada, `missing` en las
dimensiones de calidad de posicion, el carril live declarado ausente— y en todos
los casos evita el mismo fallo: que alguien lea "0.0" y crea que se midio.

**Tres bugs de diseno los encontraron los tests, no produccion.**
1. **Lead-lag** reportaba liderazgo inventado entre series simultaneas (Bloque 5).
2. **El IOC** ganaba siempre por un error de contabilidad, no por merito (Bloque 6).
3. **El tope de exposicion se tragaba la reduccion de riesgo** por calidad de
   dato, y la proteccion desaparecia justo en las operaciones mas grandes
   (Bloque 11). En el mismo bloque, la media diluia las senales criticas y un
   motor ciego quedaba escondido detras de siete senales sanas.

**Dos cosas que el enunciado pedia y NO se hicieron, con su razon escrita.**
- **SHAP** (Bloque 13): exigiria una dependencia binaria pesada que solo cubre
  parte del catalogo de modelos, obligando a caer a otra metrica para el resto —
  dos numeros distintos llamados igual. Se usa importancia por permutacion.
- **Un test ADF de cointegracion** (Bloque 5): necesita tablas de valores
  criticos, y fingir un p-valor seria peor que no darlo. Se reporta la vida media
  del residuo, que es lo accionable.

**Lo que queda inerte por falta de datos, y no por falta de codigo.** El Bloque 3
entero (microestructura) no tiene con que alimentarse mientras el broker sea MT5,
que no publica libro. Es codigo correcto esperando una decision de fuente de
datos que no tomaba este bloque.

**Live trading sigue deshabilitado.** Ningun bloque toco el guard, y el Bloque 15
—el unico que roza el tema— expone el invariante para poder verificarlo desde
fuera, con un test que comprueba que el modo resuelto sigue siendo `paper`.

**ADRs nuevos:** 100 a 114. **Tests nuevos:** 304.

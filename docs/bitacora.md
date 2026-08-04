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

# Arquitectura y decisiones técnicas — Quant Engine V2 (Fase 1)

Cada decisión sigue el formato ADR corto: contexto → decisión → consecuencias.

---

## ADR-001 · Arquitectura orientada a eventos con Event Bus propio

**Contexto.** Los módulos (mercado, estrategias, riesgo, ejecución, paper,
notificaciones) deben comunicarse sin acoplarse. Se evaluó usar una librería
(p. ej. un broker externo tipo Redis pub/sub) frente a un bus in-process.

**Decisión.** Event Bus propio sobre `asyncio` (`app/core/events/bus.py`):
cola interna, worker de despacho, suscripción por tipo (con subclases) y
comodín, aislamiento de errores por handler, dead letters y métricas.

**Consecuencias.** Cero dependencias externas para el flujo crítico; latencia
in-process; los handlers rotos no tumban el sistema. Si en el futuro se
requiere distribución entre procesos, el contrato (`publish`/`subscribe`) se
mantiene y solo cambia la implementación.

## ADR-002 · Composition root único con contenedor DI minimalista

**Contexto.** SOLID exige inversión de dependencias, pero los frameworks DI
de Python agregan magia difícil de depurar.

**Decisión.** Contenedor explícito por tipo (`app/core/container.py`) usado
**solo** en `app/engine/bootstrap.py`. Los módulos reciben dependencias por
constructor; ninguno importa implementaciones de otro módulo — solo
interfaces (`app/core/interfaces`) o eventos.

**Consecuencias.** Grafo de dependencias visible en un solo archivo;
testeabilidad total (todo se inyecta); sin dependencias circulares por diseño
(regla: todos dependen de `core`, `core` no depende de nadie).

## ADR-003 · Ciclo de vida uniforme (`Service`) supervisado por watchdog

**Contexto.** Un sistema 24/7 necesita arranque/apagado ordenado, detección
de módulos congelados y reinicios automáticos.

**Decisión.** Clase base `Service` (template method `start`/`stop` con
estados) en `app/core/lifecycle.py`. El motor arranca los servicios en orden
(bus → cache → notificaciones → scheduler → watchdog → health → API) y los
apaga en orden inverso. Cada servicio se registra en el `Watchdog` con un
callback de reinicio (`stop`+`start`); un job del scheduler emite heartbeats
solo si el `healthcheck()` del servicio pasa.

**Consecuencias.** Congelamiento ⇒ evento `ModuleFrozen` + reinicio
automático con presupuesto (`max_restarts`); agotado el presupuesto, el
estado pasa a `exhausted` y el Health Monitor reporta `unhealthy`.

## ADR-004 · Configuración: pydantic-settings, ambientes y precedencia

**Contexto.** Nada puede estar hardcodeado; cuatro ambientes con
configuraciones independientes; secretos fuera del repositorio.

**Decisión.** `Settings` raíz con secciones tipadas (pydantic-settings),
prefijo `QE_` y anidamiento `__`. Precedencia: variables del proceso →
`config/<ambiente>.env` (overlay, no versionado-sensible) → `.env` (secretos
locales, ignorado por git). Secretos con `SecretStr` (no se filtran en repr/logs).

**Consecuencias.** `testing` fuerza Discord OFF aunque `.env` lo active — las
pruebas jamás golpean el webhook real. Cambiar de ambiente = 1 variable.

## ADR-005 · Notificaciones: servicio desacoplado, canal único Discord

**Contexto.** Regla global del proyecto: todo por Discord Webhook; nada de
Telegram/Slack/email/SMS, pero extensible a futuro.

**Decisión.** `NotificationService` opera contra la interfaz
`NotificationChannel`; el único canal registrado es `DiscordWebhookChannel`
(embeds con color por severidad, manejo de HTTP 429 con `retry_after`,
reintentos con backoff). Entrega *best effort*: un canal caído nunca
interrumpe la operación. La URL del webhook vive en `.env` como `SecretStr`.

**Consecuencias.** Añadir un canal en el futuro = implementar la interfaz y
registrarlo en el bootstrap; cero cambios en la lógica de negocio.

## ADR-006 · Cache degradable: Redis primario, memoria como fallback

**Contexto.** Requisito: la lógica debe funcionar aunque Redis caiga.

**Decisión.** `CacheService` con backend primario (Redis) y fallback en
memoria. Un fallo del primario conmuta a memoria (log + flag `degraded`) y se
reintenta el primario tras un cooldown configurable. Ninguna operación de
cache lanza excepción por caída del backend.

**Consecuencias.** Redis es una optimización, nunca un punto único de fallo.

## ADR-007 · Persistencia preparada sin tablas

**Contexto.** Fase 1 no define modelos de datos, pero la arquitectura debe
quedar lista (SQLAlchemy, Alembic, pool, repositorios desacoplados).

**Decisión.** `DatabaseManager` con engine async **perezoso** (no conecta
hasta el primer uso), sesiones transaccionales, `Base` declarativa con
convención de nombres estable, Alembic async configurado leyendo la URL desde
la configuración central, y contrato `Repository` genérico.

**Consecuencias.** El sistema arranca sin base de datos; las fases futuras
solo añaden modelos + `alembic revision --autogenerate`.

## ADR-008 · Logging: stdlib con rotación, módulo y JSON opcional

**Contexto.** Se requieren niveles estándar, rotación, logs por módulo,
estructura y opción JSON — sin dependencias extra.

**Decisión.** `logging` estándar orquestado en `app/logging/setup.py`:
consola + `app.log` + `errors.log` (rotados por tamaño) + un archivo por
módulo declarado en configuración. `JsonFormatter` propio (una línea JSON por
registro) activable por configuración. `RecentErrorsBuffer` (handler en
memoria) alimenta el Health Monitor con los últimos errores.

**Consecuencias.** Cero dependencias; en `paper`/`production` los logs son
JSON (ingestión futura); en desarrollo, texto legible.

## ADR-009 · API embebida en el motor (uvicorn como Service)

**Contexto.** El dashboard (fase futura) necesita REST + WebSocket. Correr la
API como proceso aparte duplicaría el estado (bus, health).

**Decisión.** `ApiService` ejecuta `uvicorn.Server` como task dentro del
event loop del motor, sirviendo la app de `create_app(settings, container)`.
El WebSocket `/ws/events` retransmite el bus con colas acotadas por cliente
(un cliente lento pierde eventos, no bloquea al sistema).

**Consecuencias.** Un solo proceso backend; la API comparte ciclo de vida y
supervisión (watchdog) con el resto de servicios.

## ADR-010 · Bitácora vía DocumentationService (Notion preparado)

**Contexto.** La bitácora debe mantenerse desde ya y en el futuro
sincronizarse con Notion.

**Decisión.** `DocumentationService` contra la interfaz
`DocumentationBackend`; backend Markdown activo (`docs/bitacora.md`), backend
Notion declarado (config `NotionSettings` incluida) pero sin implementar.

**Consecuencias.** La integración Notion será registrar un backend nuevo.

## ADR-011 · Docker Compose con perfiles

**Decisión.** Servicios por defecto: backend + PostgreSQL + Redis. Perfiles
opt-in: `dashboard` (Next.js futuro), `timescale`, `monitoring`
(Prometheus + Grafana, provisionados). Backend con usuario no-root y
healthcheck sobre `/api/health`.

## ADR-012 · Calidad: Ruff + Black + MyPy strict + Pytest en CI

**Decisión.** Configuración única en `pyproject.toml` (línea 100, py312,
pydocstyle Google, mypy strict con overrides mínimos). GitHub Actions ejecuta
lint + formato + tipos + tests con cobertura en cada push/PR. Pipelines de
despliegue quedan para fases posteriores.

---

# Fase 2 — Data Engine

## ADR-013 · El Data Engine es la única fuente de verdad de mercado

**Contexto.** Estrategias, riesgo y dashboard necesitan datos de mercado;
si cada módulo hablara con su exchange, el acoplamiento y la duplicación
serían inmanejables y los formatos de cada broker se filtrarían por todo el
sistema.

**Decisión.** Todo dato externo entra por un proveedor
(`app/market/providers`), se normaliza en esa frontera y fluye por un único
pipeline (`TickCollector`). Los consumidores usan `MarketDataService`
(lecturas) y el Event Bus (notificaciones). Ningún módulo fuera de
`app/market/providers` conoce URLs, símbolos o formatos de exchange.

**Consecuencias.** Añadir un broker = implementar `MarketDataProvider` y
registrarlo en `ProviderRegistry`; el núcleo no cambia. Las estrategias de la
Fase 3 dirán `subscribe("BTCUSDT")` sin saber qué broker lo sirve (ruteo en
`market.symbol_provider`).

## ADR-014 · Normalización en la frontera del proveedor

**Contexto.** Binance, Bybit y OKX entregan esquemas distintos para lo mismo.

**Decisión.** Cada proveedor incluye su `Normalizer` que traduce mensajes
crudos a los modelos internos (`Ticker`, `Trade`, `Candle`, `OrderBookDelta`,
`FundingRate`, `OpenInterest`, `Liquidation`) con doble timestamp UTC
(exchange + local) y latencia derivada. El formato original muere dentro del
proveedor.

**Consecuencias.** El resto del sistema opera sobre un único vocabulario
tipado e inmutable. Los normalizadores se testean con payloads reales de cada
exchange sin tocar la red.

## ADR-015 · Conexiones WS supervisadas con reconstrucción vía REST

**Contexto.** Un stream 24/7 se cae: hace falta reconexión con backoff,
heartbeat, detección de conexión muda y reconstrucción del estado perdido.

**Decisión.** `WSConnection` implementa la máquina de estados (backoff
exponencial con jitter y tope, ping de protocolo + payload de aplicación,
staleness por silencio, resuscripción vía callback `on_connected`). El
transporte es una interfaz (`WSTransport`) — la librería `websockets` con
compresión en producción, transportes guionizados en tests. Cuando el libro
pierde secuencia, el feed lo reconstruye con el snapshot REST del proveedor
(`OrderBookResyncRequired` → `fetch_orderbook_snapshot`).

**Consecuencias.** La lógica de resiliencia se testea sin red; el streaming
nunca depende exclusivamente del WebSocket.

## ADR-016 · Pipeline único con cola no bloqueante (collector)

**Contexto.** El callback de un WebSocket jamás debe bloquearse; y todo dato
debe pasar por validación antes de publicarse.

**Decisión.** Los proveedores hacen `submit()` (put_nowait) a la cola del
`TickCollector`; un worker drena y ejecuta: validar → estado en memoria →
agregador de velas → order book → cache → storage → eventos. Cola con tope
(100k) — si se llena, se descarta y contabiliza (`dropped`): la presión de
memoria no congela la conexión.

**Consecuencias.** Backpressure explícito y medible; el fallo de un paso se
aísla por objeto sin tumbar el worker.

## ADR-017 · Validación de calidad previa a publicación

**Decisión.** `DataValidator` (puro, con estado por símbolo) descarta precios
no positivos, tamaños negativos, timestamps futuros/naive, duplicados por
trade-id, fuera-de-orden más allá de una gracia, saltos de precio > umbral y
volúmenes imposibles; marca sin descartar datos stale. Todo issue se
contabiliza y los descartes publican `DataQualityAlert`.

**Consecuencias.** Las velas y el estado nunca se contaminan con datos
corruptos; las anomalías quedan trazadas para diagnóstico.

## ADR-018 · Persistencia batched con spill a disco

**Contexto.** Guardar cada tick con un INSERT por evento mataría la DB; y la
DB puede no estar disponible sin que el Data Engine deba detenerse.

**Decisión.** `MarketDataWriter` bufferiza ticks/velas y hace flush por lotes
(intervalo/tamaño configurables); las velas upsertean con
`ON CONFLICT DO NOTHING` sobre la clave (symbol, provider, timeframe, start).
Si la DB falla, los lotes se derraman a JSONL (`data/spill/`) y el sistema
sigue. Primeras tablas reales: `market_ticks` y `market_candles` (migración
Alembic 0001).

**Consecuencias.** Sin pérdida de eventos ante caídas de DB; el costo es
reconciliar los spills manualmente (tarea futura).

## ADR-019 · Estado vivo en memoria + cache Redis como segunda copia

**Contexto.** Las lecturas calientes (último precio, libro, velas) deben ser
O(1) y sobrevivir a un reinicio del proceso lo mejor posible.

**Decisión.** `MarketStateStore` (dicts/deques en proceso) es la fuente de
lectura primaria del `MarketDataService`; `MarketCache` (Redis vía el
CacheService degradable de Fase 1) guarda el último valor de todo con claves
`mkt:*`. Redis caído = el sistema sigue en memoria.

**Consecuencias.** Lecturas sin I/O en el camino caliente; el dashboard u
otros procesos pueden leer Redis sin tocar el motor.

## Riesgos conocidos (Fase 2)

- **Un solo worker de pipeline**: a throughput extremo (varios libros L2 de
  alta frecuencia) el collector puede quedarse atrás; mitigación medible vía
  `queue_depth`/`dropped` y particionado por símbolo si hace falta.
- **Klines del proveedor vs agregador local**: ambas fuentes emiten
  `CandleClosed` (distinguidas por `candle_source`); los consumidores deben
  elegir una para no duplicar.
- **Spill sin reconciliación automática**: los JSONL de `data/spill/` no se
  reinyectan solos a la DB todavía.
- **Proveedores preparados** (Bitget/OANDA/MT5/IBKR) declaran capacidades
  pero fallan al arrancar con error claro hasta implementarse.

## Próximas tareas (Fase 3)

- ✅ Quant Core + Strategy Engine consumiendo `MarketDataService`/eventos.
- Backfill histórico usando `fetch_candles` + `MarketDataRepository`.
- Implementar MT5/OANDA para XAUUSD real.
- Reconciliación automática de spills.

---

# Fase 3 — Quant Core + Strategy Engine

Flujo de decisión (el "cerebro"; sin ejecución de órdenes en esta fase):

```
 NewTick / CandleClosed / timer          (eventos del Data Engine)
        │
        ▼
 StrategyEngine ──► ejecuta cada estrategia (plugin) con su cadencia,
        │           aislada y sin bloquear a las demás
        ▼
 BaseStrategy.analyze(ctx) ──► StrategySignal | None
        │        ctx = MarketDataService + FeatureStore + MarketContext
        ▼
 SignalEngine ──► valida, deduplica (supersede), prioriza, expira
        │                                   │
        ▼                                   ▼
 DecisionEngine                    SignalHistoryStore (+HistoryWriter→DB)
   ├── MarketContextEngine (sesiones, volatilidad, spread, noticias, calidad)
   ├── RegimeDetector (trending/ranging/breakout/reversal/…)
   ├── ConsensusEngine (majority/weighted/average/dynamic/regime)
   ├── ConfidenceEngine (confianza ≠ score, con desglose)
   └── FilterChain (session/spread/volatility/liquidity/news/drawdown/correlation)
        │
        ▼
 Decision (única, SIEMPRE explicable) ──► DecisionGenerated → bus → dashboard
```

## ADR-020 · Las estrategias no deciden: framework de evaluación pura

**Contexto.** El requisito central de la fase: "una estrategia no compra ni
vende; responde si existe una oportunidad y con qué confianza".

**Decisión.** `BaseStrategy` (`app/engine/interfaces/strategy.py`) obliga a
devolver una `StrategySignal` estructurada (dirección, confianza 0-1, score
0-100, zona de entrada, SL/TP, razones, advertencias, expiración) o `None`.
Las estrategias solo ven un `AnalysisContext` inmutable (datos + features +
contexto); no tienen acceso al bus ni a nada que ejecute. El único autorizado
a decidir es el `DecisionEngine`.

**Consecuencias.** Ninguna estrategia puede operar por sí misma ni saltarse
filtros/consenso; toda señal es auditable por diseño (razones obligatorias —
el validador rechaza señales sin explicación).

## ADR-021 · Plugins descubiertos en runtime, aislados del núcleo

**Contexto.** Añadir una estrategia no debe tocar el núcleo.

**Decisión.** `PluginLoader` escanea directorios configurados
(`quant.plugin_dirs`, por defecto `app/strategies/`) e importa cada `*.py`
no-privado buscando subclases de `BaseStrategy`. Un plugin roto se loguea y
se salta — jamás impide cargar el resto. Gestión en caliente vía QuantCore:
`load_strategy` / `unload_strategy` / `enable_strategy` / `disable_strategy`.

**Consecuencias.** Añadir estrategia = soltar un archivo. Nombres duplicados
conservan el primero (determinista por orden alfabético).

## ADR-022 · Cadencias por estrategia sin bloqueo mutuo

**Contexto.** Cada estrategia puede necesitar frecuencia distinta (tick,
segundo, vela cerrada, intervalo) y una lenta no puede frenar a las demás.

**Decisión.** `Cadence` declarativa en la clase de la estrategia. El
StrategyEngine despacha por suscripción al bus (`NewTick`, `CandleClosed`
filtrando timeframe) o por jobs del scheduler (intervalos). Cada evaluación
corre como task propia con lock por estrategia: si sigue corriendo cuando
llega el siguiente disparo, el disparo se salta y se contabiliza
(`skipped`). Tiempos por ejecución (última/EMA) quedan en `StrategyStats`
para detectar cuellos de botella.

**Consecuencias.** Decenas de estrategias concurrentes sin interferencia; la
excepción de una queda aislada (`StrategyFailed`) y las demás siguen.

## ADR-023 · Feature Store con cálculo único por ventana

**Contexto.** Con muchas estrategias, recalcular ATR/VWAP/EMA por cada una
multiplicaría el costo por N.

**Decisión.** `FeatureStore` central: proveedores por nombre (corrutinas
`(symbol, params) → float | None`) cacheados por (feature, símbolo, params)
con TTL; métricas hits/misses para detectar cálculo duplicado. Integradas:
last_price, spread_bps, atr, atr_pct, ema, vwap, volume, delta, cvd,
book_pressure, imbalance, open_interest, funding_rate. Las estrategias
registran las suyas en `initialize()`.

**Consecuencias.** Una variable se calcula una vez por ventana sin importar
cuántas estrategias la pidan.

## ADR-024 · Consenso con algoritmo intercambiable

**Contexto.** No operar por una sola estrategia; el método de agregación debe
poder cambiarse sin tocar el resto.

**Decisión.** Interfaz `ConsensusAlgorithm` + 5 implementaciones:
`majority_voting`, `weighted_voting`, `weighted_average` (con signo),
`dynamic_weighting` (peso × rendimiento reciente del historial) y
`regime_weighting` (peso × multiplicador por régimen desde configuración).
`ConsensusEngine` selecciona por configuración (`quant.consensus.method`),
permite `set_method`/`register` en caliente y expone pesos configurables por
estrategia.

**Consecuencias.** Cambiar la política de agregación es 1 línea de config;
los algoritmos son funciones puras y testeables en aislamiento.

## ADR-025 · Confianza separada del score

**Contexto.** Requisito explícito: score alto con confianza baja ⇒ no operar.

**Decisión.** `ConfidenceEngine` calcula 0-1 con desglose por factor
(ponderaciones configurables): confianza propia de las señales, acuerdo del
consenso, calidad de datos (conexión + frescura), liquidez (spread/volumen),
volatilidad, confirmaciones y rendimiento reciente. El Decision Engine exige
`min_score` **y** `min_confidence` por separado.

**Consecuencias.** El desglose viaja en cada `Decision`
(`confidence_breakdown`), así el "por qué" de la confianza es visible.

## ADR-026 · Contexto y régimen como servicios compartidos

**Contexto.** Estrategias, filtros y consenso necesitan la misma foto del
mercado; calcularla N veces sería inconsistente además de caro.

**Decisión.** `MarketContextEngine` compone un `MarketContext` inmutable por
símbolo: régimen (vía `RegimeDetector` con heurísticas documentadas —
efficiency ratio, ratio ATR corto/largo, ruptura de rango, reversión),
sesiones activas por hora UTC (solapables, con cruce de medianoche),
clasificación de volatilidad por ATR%, spread elevado, volumen suficiente,
ventanas de noticias y calidad del dato. Todo sale del Feature Store (cálculo
único).

**Consecuencias.** Todos los módulos razonan sobre el mismo contexto; el
régimen modula pesos del consenso (`regime_weighting`) sin acoplarse a las
estrategias.

## ADR-027 · Decisión única, siempre explicable

**Contexto.** Prohibido responder "no operar" a secas.

**Decisión.** `DecisionEngine.evaluate(symbol)` produce SIEMPRE una
`Decision` con explicación estructurada: consenso (método/score/acuerdo),
confianza con desglose, régimen, sesiones, señales consideradas, conflictos
detectados y — si no se opera — la lista completa de causas (umbrales
incumplidos y filtros bloqueantes, cada filtro con su razón). La cadena de
filtros NO se corta en el primer bloqueo: se evalúan todos para reportarlos.

**Consecuencias.** Cada decisión (aceptada o no) es auditable en
`/api/engine/decisions`; los eventos `ConsensusReached`, `FilterTriggered` y
`DecisionGenerated` la publican al bus.

## ADR-028 · Historial sin borrado, con persistencia batched

**Contexto.** "Guardar todas las señales. No eliminar información."

**Decisión.** `SignalHistoryStore` retiene en memoria (anillo configurable)
toda señal con su ciclo de vida (accepted/rejected/expired/superseded, con
razones y vida útil) y toda decisión. Un `signal_sink` conecta el store al
`HistoryWriter` (mismo patrón batched+spill del market writer) hacia las
tablas `strategy_signals` y `engine_decisions` (migración Alembic 0002). El
historial además alimenta el factor de rendimiento reciente por estrategia
(consenso dinámico y confianza).

**Consecuencias.** Nada se pierde ni con la DB caída (spill JSONL); el
rendimiento por estrategia se calcula de datos, no de opiniones. Cuando
exista PnL real (Fase 5), `performance_factor` pasará a usarlo.

## ADR-029 · Indicadores como funciones puras, cacheados vía Feature Store

**Contexto.** Fase 4 exige SMC, order flow, VWAP, volume profile, ATR,
momentum, market structure y liquidez, usados por 20 estrategias sin
recalcular nada y con resultados deterministas y testeables.

**Decisión.** Todo cálculo vive en `app/analytics/indicators/` como
funciones puras sobre los modelos de mercado (velas/trades/libro), sin I/O
ni estado. El Feature Store las registra como features (`smc`, `structure`,
`liquidity`, `order_flow`, `volume_profile`, `vwap_session`,
`anchored_vwap`, `delta`, `cvd`, …) con cálculo único por ventana (TTL).
`app/strategies/shared/api.py` expone los nombres estables de la
especificación (`detect_liquidity()`, `detect_fvg()`, `calculate_vwap()`,
`calculate_cvd()`, …) como fachada sobre el store.

**Consecuencias.** Llamar N veces un indicador en la misma ventana cuesta un
cálculo; los tests unitarios prueban matemática sin mocks de red; cualquier
estrategia futura consume la misma fuente (nada de indicadores duplicados).

## ADR-030 · `QuantStrategy`: la subclase solo implementa `evaluate()`

**Contexto.** 20 estrategias con el mismo contrato (analizar → puntuar →
confiar → explicar → proponer niveles) no pueden repetir el pipeline 20
veces ni esconder constantes en el código.

**Decisión.** `app/strategies/base/quant_strategy.py` implementa el pipeline
completo: pre-chequeos (datos mínimos, calidad, spread) → `evaluate()` de la
subclase (devuelve un `Assessment`: dirección + calidad/fortaleza/contexto/
probabilidad/riesgo + razones + niveles) → confirmaciones → score 0-100 con
pesos configurables → confianza multi-factor → `StrategySignal` estructurada.
TODOS los umbrales salen de `parameters` (capas: base → `default_parameters`
de la subclase → configuración externa `QE_QUANT__STRATEGIES__<nombre>__…`),
listos para el optimizador de la fase de ML.

**Consecuencias.** Una estrategia nueva son ~100 líneas de análisis puro; el
formato de señal, la explicabilidad y la configurabilidad son uniformes y no
opcionales. Nunca se responde solo BUY/SELL.

## ADR-031 · Confirmaciones bajo demanda, nunca silenciosas

**Contexto.** Cada estrategia puede exigir confirmaciones adicionales
(delta, CVD, volumen, spread, volatilidad, sesión, régimen, libro, volume
profile) sin acoplarse a cómo se calculan.

**Decisión.** `ConfirmationEngine` evalúa las confirmaciones declaradas en
el parámetro `confirmations` contra Feature Store y contexto, con dirección.
Las aprobadas se añaden a las razones ("Confirmado mediante X"); las
fallidas marcan `required_confirmation=True` y generan la advertencia
"Falta confirmación de X" — la señal viaja igualmente al motor de consenso,
que decide qué hacer con ella.

**Consecuencias.** La explicación estructurada de la especificación (razones
+ confirmaciones faltantes + confianza) sale del pipeline, no de prosa
ad-hoc; añadir una confirmación nueva es un método más en un solo sitio.

## ADR-032 · Evaluación continua con operaciones virtuales (no operativa)

**Contexto.** La fase exige estadísticas por estrategia (win rate, profit
factor, expectativa, drawdown, falsas señales, tiempo en operación) sin usar
todavía nada de eso para operar.

**Decisión.** `PerformanceTracker` (`app/engine/evaluation/`) abre una
"operación virtual" por cada señal aceptada con niveles y la resuelve contra
las velas posteriores (TP/SL/timeout, expresado en R). Acumula estadísticas
por estrategia en memoria con snapshot JSON periódico
(`data/performance/strategy_stats.json`) y expone `factor()` — el hook que
el consenso dinámico usará en fases posteriores para subir/bajar el peso de
una estrategia sin tocar su código.

**Consecuencias.** Cuando llegue el PnL real (paper/live) solo cambia la
fuente de resultados, no el contrato; mientras tanto ya se acumula historia
comparable entre estrategias.

## ADR-033 · Detecciones como eventos de primera clase

**Contexto.** El dashboard y las fases futuras necesitan ver QUÉ detectó el
sistema (sweeps, FVGs, order blocks, VWAP, delta/CVD, volume profile,
momentum), no solo la señal final.

**Decisión.** Eventos dedicados (`LiquidityDetected`, `FVGDetected`,
`OrderBlockDetected`, `VWAPCalculated`, `DeltaCalculated`, `CVDCalculated`,
`VolumeProfileUpdated`, `MomentumDetected`, `StrategyScoreUpdated`) en
`app/engine/events/detections.py`. Las estrategias los acumulan en
`ctx.detections` durante `evaluate()` y el Strategy Engine los publica al
bus junto con `StrategyExecuted`/`StrategyScoreUpdated` — las estrategias
no tocan el bus directamente.

**Consecuencias.** El WebSocket del dashboard ya emite el flujo de
detecciones; correlacionar "qué se vio" con "qué se decidió" es una consulta
de eventos, no arqueología de logs.

## ADR-034 · Solo Paper Trading: el modo se fuerza en el propio setting

**Contexto.** La regla de oro de la Fase 5 es que ninguna orden llegue a un
broker real hasta cumplir criterios estadísticos definidos, y esa regla no
puede depender de que nadie olvide un `if` en el código de arranque.

**Decisión.** `ExecutionSettings.resolved_mode()` fuerza cualquier valor
distinto de `paper` a `paper` en el propio objeto de configuración — no hay
ninguna rama de código que sepa hablar con un broker real, así que no existe
"live" que deshabilitar: no se implementó.

**Consecuencias.** Activar trading real en el futuro exige escribir un
adaptador de broker nuevo y quitar explícitamente este forzado, nunca un
simple cambio de variable de entorno.

## ADR-035 · Ejecución desacoplada del Strategy/Decision Engine

**Contexto.** Ninguna estrategia debe poder enviar una orden directamente;
el flujo tiene que pasar siempre por el mismo camino auditable.

**Decisión.** Flujo único y unidireccional dirigido por eventos:
`DecisionGenerated` → `RiskManager` → `ExecutionEngine` → `PaperBroker` →
`PositionManager`/`PortfolioManager` → `TradeJournal` → eventos → Discord.
`ExecutionEngine` es el único suscriptor de `DecisionGenerated` con permiso
de abrir posiciones; vive en `app/execution/`, un árbol de 14 módulos de
responsabilidad única (models, events, commission, slippage, latency,
sizing, paper_engine, position_manager, portfolio_manager, order_manager,
risk_manager, journal, performance, notifications).

**Consecuencias.** Añadir una estrategia nueva no toca nada de ejecución;
auditar "por qué se abrió esta posición" es seguir la cadena de eventos, no
leer el código de la estrategia.

## ADR-036 · Paper Engine de alta fidelidad: nunca ejecución perfecta

**Contexto.** Un simulador que siempre rellena al precio pedido enseña
métricas que no sobreviven a un broker real.

**Decisión.** `PaperBroker.execute()` usa bid/ask reales, aplica slippage
adverso (nunca mejora el precio), latencia con deriva de precio, comisión
por operación y probabilidades de rechazo/ejecución parcial/gap. Los
**cierres** se ejecutan con `allow_reject=False` y `allow_partial=False` —
una posición nunca queda a medio cerrar ni bloqueada por un rechazo, solo
las aperturas están sujetas a fricción realista.
Motores independientes e intercambiables: `commission/` (por nocional /
por unidad / fijo, overrides por símbolo y maker/taker), `slippage/`
(según volatilidad, liquidez, tamaño, sesión y tipo de orden), `latency/`
(red + broker + exchange + interna, con jitter).

**Consecuencias.** Las métricas de paper trading son un límite inferior
razonable de lo que pasaría en real, no un techo optimista; el coste de
fricción es visible y auditable por componente (comisión vs slippage vs
latencia) en el Trade Journal.

## ADR-037 · Risk Manager como veto único, con kill switch y circuit breaker

**Contexto.** El sizing y la ejecución no deben poder abrir una posición que
viole el riesgo del portafolio; tiene que haber un único punto que apruebe o
rechace, con motivo explícito.

**Decisión.** `RiskManager.check()` recibe una `RiskQuery` y devuelve un
`RiskCheck` con razón obligatoria. Cubre: riesgo por operación; pérdida
diaria/semanal/mensual; pérdidas consecutivas; nº máximo de posiciones
abiertas; exposición total, por símbolo y por correlación; filtros de
spread y liquidez. Dos mecanismos de emergencia adicionales: **kill switch**
(drawdown supera el umbral → cierra todas las posiciones) y **circuit
breaker** (pérdida rápida en ventana móvil → bloquea nuevas aperturas).

**Consecuencias.** `ExecutionEngine` nunca decide si el riesgo es aceptable;
si el Risk Manager rechaza, se publica `RiskTriggered`/`KillSwitchTriggered`/
`CircuitBreakerTriggered` y Discord lo notifica de inmediato — no hay
apertura silenciosa que sortee el límite.

## ADR-038 · Position sizing como estrategia intercambiable, nunca hardcodeada

**Contexto.** Cada símbolo y cada estilo de gestión necesita un método de
sizing distinto (monto fijo, % de capital, ATR, riesgo fijo, riesgo dinámico
por confianza, Kelly parcial), y el motor de ejecución no debería saber cuál
se está usando.

**Decisión.** `sizing/` implementa cada método como función pura
intercambiable por configuración (`QE_EXECUTION__SIZING__*`), con un tope de
exposición por operación (`max_position_pct`) aplicado siempre después del
cálculo, sin excepción por método.

**Consecuencias.** Cambiar de sizing es un cambio de configuración, no de
código; el tope de exposición es la última línea de defensa incluso si un
método de sizing calcula mal.

## ADR-039 · Trade Journal append-only como fuente de verdad de cada operación

**Contexto.** El rendimiento, la conformidad y la auditoría dependen de que
cada operación quede registrada exactamente como ocurrió, sin poder
reescribirse después.

**Decisión.** `journal/` escribe cada operación (precios, costes de
comisión/slippage/spread, R, ATR, régimen, score, confianza, razones de
entrada/salida) en memoria y en JSON Lines de solo-append; nunca se edita ni
se borra un registro existente. `performance/` calcula sus métricas (win
rate, profit factor, expectativa, R:R, drawdown máximo, Sharpe, Sortino,
Calmar, Ulcer Index, recovery factor, tiempo medio en mercado, actividad por
día/sesión) siempre leyendo del journal, nunca de un estado mutable aparte.

**Consecuencias.** Cualquier discrepancia entre "lo que dice el dashboard" y
"lo que realmente pasó" se resuelve releyendo el journal; no hay una segunda
fuente de verdad que se pueda desincronizar.

## ADR-040 · Portfolio Manager como contabilidad de margen explícita

**Contexto.** Balance, equity y exposición necesitan una definición única y
consistente para que Risk Manager, Performance Engine y el dashboard nunca
calculen cifras distintas para el mismo instante.

**Decisión.** `portfolio_manager/` es la única fuente de balance
(=cash), equity (=balance + PnL flotante), capital usado (Σcoste/leverage
por posición abierta), capital libre, drawdown (contra el equity pico
histórico) y exposición. Todo lo demás (Risk Manager, Performance Engine,
`/api/execution/portfolio`) lee de aquí, nunca recalcula por su cuenta.

**Consecuencias.** Un cambio en la fórmula de equity o drawdown se hace en
un solo lugar y se propaga a todo el sistema sin más wiring.

## ADR-041 · Notificaciones de ejecución desacopladas vía suscripción al bus

**Contexto.** El motor de ejecución no debe saber que Discord existe ni
formatear un solo embed; las notificaciones son responsabilidad de otro
módulo, igual que en Fase 1.

**Decisión.** `notifications/ExecutionNotifier` es un `Service` que se
suscribe al bus de eventos de ejecución (posición abierta/cerrada, stop
movido, break-even, trailing, rechazo, riesgo, kill switch, circuit
breaker) y traduce cada uno a un embed de Discord mediante plantillas,
además de emitir reportes periódicos (horario/diario). El motor de
ejecución solo publica eventos; nunca importa el `NotificationService`.

**Consecuencias.** Añadir un canal de notificación nuevo (o cambiar el
formato de los embeds) no toca ni una línea de `execution_engine/` ni de
`risk_manager/`.

## ADR-042 · El backtest reutiliza el Execution Engine, no lo clona

**Contexto.** La especificación de la Fase 6 exige que la simulación se
comporte exactamente igual que el paper trading y que no existan dos motores
de ejecución que puedan divergir.

**Decisión.** El backtest conduce el mismo `ExecutionEngine` de la Fase 5
sobre una `MarketDataService` histórica (un `MarketStateStore` en memoria que
se puebla vela a vela) con `bus=None` y sin `MarketContextEngine`. Los mismos
`process_decision()` y `manage_once()` que operan en vivo son los que operan
en el backtest; comisiones, slippage, latencia y riesgo son los mismos
objetos. `app/backtesting/simulator/` replica el wiring de
`bootstrap._build_execution` sin el contenedor DI.

**Consecuencias.** Una mejora en el motor de ejecución mejora el backtest
automáticamente; es imposible que el simulador y el paper trading calculen un
PnL distinto para la misma secuencia de precios.

## ADR-043 · Reloj inyectable para dar fidelidad temporal sin tocar el motor

**Contexto.** El tiempo en mercado, las salidas por tiempo y los timestamps de
las operaciones dependen de `utc_now()`. En un backtest deben reflejar el
momento histórico reproducido, no el reloj de pared, y no queremos reescribir
cada módulo de la Fase 5 para pasarles un reloj.

**Decisión.** `app/utils/time.py` expone un seam: `utc_now()` lee de un
proveedor global inyectable (`use_clock`/`set_clock`). El backtest instala un
`ReplayClock` durante la corrida y lo retira al terminar. En producción el
proveedor es `None` y el comportamiento es idéntico al reloj de pared.

**Consecuencias.** `Position.opened_at`, `holding_seconds()` y los resets
diarios/semanales del Risk Manager funcionan en tiempo simulado sin ninguna
rama especial "si es backtest". El reloj es global (una corrida por proceso);
correr backtests en paralelo exige procesos separados — aceptable para un lab.

## ADR-044 · Camino intrabar OHLC para no perder stops dentro de la vela

**Contexto.** Si solo se marca al cierre de cada vela, un stop tocado en el
mínimo intravela pasaría desapercibido hasta un cierre posterior, inflando el
rendimiento.

**Decisión.** Por cada vela, el motor recorre un camino de precios
`open → extremo adverso → extremo favorable → close` (mínimo primero en velas
alcistas, máximo primero en bajistas) y llama a `manage_once()` en cada punto,
de modo que stops, objetivos y trailing puedan dispararse dentro de la vela.

**Consecuencias.** Es una aproximación conservadora estándar, no una
reproducción tick-a-tick; la reproducción con order-book/tick real queda como
estructura preparada. Las entradas se ejecutan al cierre de la vela de señal.

## ADR-045 · Métricas: extender el Performance Engine, no reimplementarlo

**Contexto.** El Performance Engine de la Fase 5 ya calcula win rate, PF,
Sharpe, Sortino, Calmar, Ulcer, recovery y drawdown. El laboratorio añade
SQN, MAR, Kelly, payoff, rachas, drawdown medio, exposición y alpha/beta.

**Decisión.** `StatisticsEngine` compone el `PerformanceReport` existente y le
añade las métricas nuevas; `QuantStatistics.to_dict()` fusiona ambos en un
único diccionario plano. Nada de lo ya calculado se reimplementa. Beta (frente
a un benchmark de mercado) queda como estructura preparada.

**Consecuencias.** Una única fuente de verdad para las métricas compartidas;
el dashboard y el pipeline de calificación leen el mismo diccionario.

## ADR-046 · Optimización agnóstica del objetivo; técnicas caras, honestas

**Contexto.** Hay que optimizar cualquier parámetro con varias técnicas
(grid, random, genético, bayesiano, Optuna) sin acoplar el optimizador a cómo
se evalúa cada combinación.

**Decisión.** El optimizador recibe un `ParameterSpace` y una función objetivo
`params → float` a maximizar. Grid, random y genético son funcionales; el
genético memoriza evaluaciones para no repetir backtests caros. Bayesiano y
Optuna son estructura preparada: **fallan con un error claro** si se invocan
sin su dependencia, en vez de fingir un resultado.

**Consecuencias.** El laboratorio nunca reporta una optimización que no hizo;
añadir una técnica nueva es implementar la interfaz `Optimizer`.

## ADR-047 · Walk-forward: optimizar in-sample, medir out-of-sample

**Contexto.** Optimizar sobre todo el histórico produce sobreajuste. La única
medida honesta del edge es el rendimiento en datos que la optimización no vio.

**Decisión.** `WalkForwardAnalysis` genera ventanas rolling/expanding/anchored,
optimiza en el tramo de entrenamiento y evalúa con esos parámetros el tramo de
validación. Agrega los resultados OOS y calcula estabilidad (consistencia
entre pliegues) y eficiencia (OOS frente a IS). El `OverfittingDetector`
complementa con avisos de curve fitting, degradación IS→OOS, data snooping y
parámetros en el borde del dominio.

**Consecuencias.** El sistema distingue una estrategia robusta de una ajustada
a una ventana concreta antes de arriesgar nada, ni siquiera en paper.

## ADR-048 · Strategy Qualification Pipeline como puerta única a paper

**Contexto.** Se quiere que el sistema evolucione de forma controlada y que
ninguna estrategia sobreajustada llegue a paper trading.

**Decisión.** `StrategyQualificationPipeline` corre una batería automática y
configurable —validación técnica, backtest, criterios mínimos, Monte Carlo,
robustez por tramos, comparación contra benchmarks y walk-forward— y devuelve
una aprobación o rechazo **con motivos explícitos**, nunca un booleano opaco.
Los umbrales viven en `QE_BACKTEST__CRITERIA__*`. El resultado se puede
registrar como experimento permanente.

**Consecuencias.** "Pasar el laboratorio" es un requisito auditable y
reproducible para promover una estrategia; sigue habilitando **solo paper**,
nunca live.

## ADR-049 · Experimentos append-only y parámetros versionados

**Contexto.** Reproducir y auditar la evolución del sistema exige que ningún
resultado ni configuración se pierda o se sobrescriba.

**Decisión.** `ExperimentManager` escribe cada experimento en su propio
archivo JSON (id + timestamp); nunca sobrescribe. `VersionStore` mantiene el
historial de conjuntos de parámetros por estrategia (`1.0`, `1.1`, ...) y
permite volver a una versión anterior de forma no destructiva (el rollback
añade una versión nueva copia de la pedida).

**Consecuencias.** Siempre se puede reconstruir con qué parámetros y datos se
obtuvo un resultado, y volver a una configuración que funcionó.

## ADR-050 · El ML asesora, nunca decide ni opera

**Contexto.** La Fase 7 introduce Machine Learning. El riesgo es que un modelo
—opaco por naturaleza— empiece a tomar decisiones de trading o habilite live.

**Decisión.** El `MLEngine` sólo produce recomendaciones (probabilidad de
operación buena, pesos de estrategia, alertas de deriva). Ninguna función abre o
cierra posiciones ni cambia el modo; toda salida pasa por el Decision Engine y el
Risk Manager existentes. `MLSettings.enabled` y `auto_activate` son `False` por
defecto. El sistema sigue en paper (`resolved_mode()` de la [ADR-034]).

**Consecuencias.** El ML es un complemento, no un sustituto de las reglas
cuantitativas; se puede apagar sin afectar a la operativa.

## ADR-051 · Las features son el contexto de la decisión, no el precio

**Contexto.** Predecir el precio directamente con ML es la trampa clásica y no
tiene edge fuera de muestra (verificado en la investigación previa).

**Decisión.** El `FeatureEngineer` construye un vector fijo y versionado a partir
del *contexto de la decisión* (hora/sesión, régimen, volatilidad, ATR%, spread,
score, confianza, R:R planificado, nº de confirmaciones, dirección) —todo
conocido al abrir—. El **resultado** (PnL, R, motivo de salida) es la
**etiqueta**, nunca una feature: así el modelo aprende a filtrar operaciones
malas sin mirar el futuro.

**Consecuencias.** El ML clasifica la calidad de una operación (1 buena / 0
mala), no adivina el precio; entrenamiento e inferencia comparten esquema exacto.

## ADR-052 · Feature Store profesional versionado, distinto del de mercado

**Contexto.** Ya existe un Feature Store de mercado (`app.engine.feature_store`,
cache de indicadores en vivo). El ML necesita un **catálogo** con metadatos.

**Decisión.** `app.ml.feature_store` es un catálogo versionado: cada feature
lleva nombre, versión, descripción, fuente, tipo, fecha, validez y dependencias.
Nunca sobrescribe una versión (publica una nueva) y evita recalcular reutilizando
valores vigentes según su `validity_seconds`.

**Consecuencias.** Se puede auditar de dónde sale cada variable y no se recalcula
lo que sigue vigente.

## ADR-053 · Model Registry never-overwrite con activación reversible

**Contexto.** Activar un modelo debe ser auditable y reversible al instante.

**Decisión.** El `ModelRegistry` guarda una ficha por versión (id, versión,
dataset, params, métricas, estado, autor), nunca sobrescribe y mantiene un
historial de auditoría. La activación archiva el modelo anterior y apila su id;
`rollback()` lo restaura de inmediato. Metadatos en disco (JSON); el objeto vivo
no se rehidrata (degrada con elegancia a "sólo reglas").

**Consecuencias.** Cambiar de modelo es trazable y reversible sin pérdida de
historial.

## ADR-054 · Puerta de validación: nunca activar un modelo inferior

**Contexto.** Un modelo peor que el activo no debe llegar a producción por
accidente.

**Decisión.** `evaluate_model` exige mínimos (muestras, AUC, accuracy) sobre
holdout + walk-forward y, si `require_beat_previous`, que bata al modelo activo
por al menos `min_improvement`. Un modelo que no cumple se rechaza con motivos
explícitos; incluso aprobado, sólo se autoactiva si `auto_activate` está
encendido.

**Consecuencias.** La calidad del asesor sólo puede subir; la promoción nunca es
automática sin superar la puerta.

## ADR-055 · La deriva reduce la confianza, nunca para la operativa

**Contexto.** El mercado cambia; el modelo entrenado envejece.

**Decisión.** El `DriftDetector` cubre cuatro clases —feature (PSI por columna),
concept (cambio de win rate), performance y model drift—. Ante deriva: genera
alerta, **reduce el factor de confianza** de la inferencia y **programa
reentrenamiento**; nunca detiene el trading, sólo ajusta el asesoramiento.

**Consecuencias.** El sistema se vuelve más cauto ante datos que ya no se parecen
al entrenamiento, sin cortes bruscos.

## ADR-056 · Meta Strategy Manager gobierna por configuración, no el código

**Contexto.** La mejora obligatoria de la fase: un gobierno por encima de
estrategias y ML que decida cuáles siguen activas y con qué peso.

**Decisión.** El `MetaStrategyManager` puntúa cada estrategia con evidencia
(reciente/histórica/segmentada), sube el peso de las consistentes y desactiva las
degradadas tras `disable_after_periods` evaluaciones seguidas, todo por
**configuración** (activación, prioridad, ponderación) — nunca tocando el código
de la estrategia. Sus pesos alimentan al consenso, que sigue pasando por el
Decision Engine y el Risk Manager. Guarda auditoría de cada decisión.

**Consecuencias.** El sistema se autoajusta sin reescribir estrategias y sin
saltarse ninguna validación; nunca abre ni cierra posiciones.

## ADR-057 · Modelos puros en Python y notificador ML desacoplado por eventos

**Contexto.** El VPS de bajo consumo no soporta backends pesados (numpy2/pyarrow
abortan) y el ML no debe acoplarse al canal de Discord.

**Decisión.** Los cuatro modelos base (logística, árbol, random forest, extra
trees) están en **Python puro** sin dependencias; XGBoost/LightGBM/CatBoost/redes
quedan tras la misma interfaz y fallan con mensaje claro si su librería no está.
El `MLNotifier` (un `Service`) se suscribe al Event Bus y traduce los hitos del ML
a embeds de Discord reutilizando el `NotificationService` de la Fase 1; el
`MLEngine` sólo publica eventos y no conoce al notificador.

**Consecuencias.** El ML entrena y sirve en hardware modesto y el canal de
notificación es intercambiable sin tocar el motor.

## ADR-058 · Dashboard frontend en Next.js sobre la API REST/WS existente

El dashboard vive en `dashboard/` (Next.js App Router + React + TypeScript +
TailwindCSS + shadcn/ui + TanStack Query + Zustand + TradingView Lightweight
Charts) y consume la API que ya sirve el motor (REST `GET` + WebSocket
`/ws/events`). Los tipos TS se mantienen a mano espejando los `.to_dict()` de
dominio porque las rutas devuelven `dict[str, Any]` (OpenAPI pobre). Cada panel
degrada con elegancia ante `loading` / error / `503 "not enabled"` / vacío.

## ADR-059 · Capa de comandos del backend con CORS ampliado

La Fase 8 convierte el dashboard en centro de control, así que el backend deja de
ser solo-lectura: se amplía el CORS a `POST/PATCH/PUT/DELETE` y se añaden rutas de
escritura (`/api/config`, `/api/engine/strategies/{name}/{enable,disable,weight}`,
`/api/backtesting/run|cancel`, `/api/integrations/*`, `/api/reports/*`), además de
exponer las acciones ML que ya existían. Las operaciones pesadas (backtests
reales) siguen ejecutándose vía `BacktestLab`/CLI; el endpoint acepta y audita.

## ADR-060 · Guard anti-live: ninguna escritura habilita live trading

Live (Fase 9) sigue deshabilitado como invariante absoluto. `app/dashboard/api/
guard.py` rechaza (403) cualquier intento de poner un modo `live/real/production`
desde un patch de configuración o comando. La seguridad no depende del CORS sino
del guard; `resolved_mode()` sigue forzando `paper` (ADR-034).

## ADR-061 · Audit log append-only (JSONL) de las acciones del dashboard

Toda acción de escritura se registra en un audit log append-only
(`app/dashboard/api/audit.py`, JSONL `logs/audit.jsonl` + ring en memoria,
expuesto en `GET /api/audit`), siguiendo el estilo del Trade Journal — sin
migración de base de datos. Cumple la regla "todas las acciones del usuario deben
registrarse para auditoría y trazabilidad".

## ADR-062 · Config runtime por overrides con whitelist

`app/dashboard/api/config_store.py` mantiene overrides de una **whitelist** curada
de rutas de settings (riesgo, paper, discord, ML, criterios de backtesting),
persistidos en JSON y reflejados por `GET /api/config`; `PATCH /api/config` valida,
coacciona el tipo, pasa por el guard anti-live y audita. Aplicar los overrides a
subsistemas ya en marcha se cablea de forma progresiva (un motor en ejecución lee
la mayoría de settings al arrancar); el store es la fuente de verdad de la
intención del operador.

## ADR-063 · Live Gate fail-closed con hash que ata la aprobación

La única puerta a live (`app/production/live/gate.py`) evalúa ~20 criterios
(estadísticos, científicos, ML, infraestructura, aprobación humana) y **nunca
responde "no" a secas**: devuelve criterio por criterio qué falta. Un valor
desconocido (`None`) cuenta como fallido (fail-closed). El reporte se identifica
por un hash de `criterios configurados + resultado de cada check`; la aprobación
del operador se firma contra ese hash, así relajar un umbral tras aprobar
invalida la aprobación en vez de heredarla. `allow_live` nace en `False`.

## ADR-064 · Kill switch, Safe Mode y Recovery persistentes y auditados

El kill switch global es disparable desde cualquier sitio (manual/auto/
programado/riesgo), **persiste en disco** (un reinicio no lo libera) y exige
actor + motivo para liberarse. Safe Mode degrada (cierra entradas nuevas,
mantiene posiciones por config) ante CPU/RAM/latencia/drawdown/errores. Recovery
rehidrata posiciones, contabilidad, riesgo y journal **antes** de que el motor
opere — nunca se arranca de cero. Todo queda en la auditoría append-only.

## ADR-065 · Documentación automática a Notion con cola de sincronización

`NotionJournalBackend` crea una página por entrada vía la API oficial. Si Notion
no responde, la entrada **no se pierde**: se encola en un JSONL en disco y un job
del scheduler (`notion_sync`) reintenta. La base sólo necesita una propiedad de
título `Name`; categoría/tags/contenido van al cuerpo como bloques, así el
backend no depende del esquema. Un fallo de Notion jamás bloquea la operación.

## ADR-066 · Discord multicanal por enrutador con webhook por canal lógico

`RoutedDiscordChannel` se registra como el único canal `discord` del servicio,
pero enruta cada notificación al webhook del canal lógico que corresponda
(sistema/trading/errores/backtesting/ml/produccion/reportes) por `channel`
explícito, por `source`, o mandando errores/críticos al canal `errores`. Un solo
webhook configurado se comporta exactamente como antes (todo al de por defecto):
multicanal es opcional y retrocompatible. Sigue siendo Discord y sólo Discord.

## ADR-067 · Backups del estado crítico con manifiesto SHA-256 verificado

`BackupService` empaqueta el **estado en disco** que el motor genera (auditoría,
journal, snapshots, registro de modelos) en un `tar.gz` con un manifiesto que
guarda un SHA-256 del archivo. La verificación recomputa ese hash: un backup
corrupto se detecta antes de necesitarlo, y `restore` **se niega** a restaurar
uno que no verifica. Rotación por retención; extracción con protección anti path
traversal. No respalda PostgreSQL (eso es `pg_dump` en el runbook de infra).

## ADR-068 · Reportes operativos automáticos, best-effort y aislados

`ReportService` arma un `OperationalReport` (PnL, capital, drawdown, win rate,
PF, trades, CPU/RAM/latencia, estado de ML/brokers/Notion/Discord) **en el
momento de emitir**, aislado por subsistema igual que los colectores de
métricas: lo que no se puede medir queda en `None`, nunca inventado. Se entrega
al canal `reportes` cada hora y cada día, y se audita. Observa; no decide.

## ADR-069 · Seguridad: rate limiting, validación de config y rotación vigilada

Middleware de API con **token bucket por IP** (exime `/metrics` y `/ws`) y
cabeceras defensivas. `validate_config` detecta configuraciones peligrosas
(secretos vacíos en producción, live sin la capa de producción) devolviendo
hallazgos con severidad en vez de lanzar. `SecretRotationManager` vigila la
antigüedad de los secretos y avisa cuando toca rotarlos — **nunca lee ni imprime
el valor del secreto**, sólo marcas de tiempo; un secreto sin marca vence
(fail-closed).

## ADR-070 · Continuous Improvement Engine (modo desarrollo permanente)

El proyecto nunca se considera "terminado". `ContinuousImprovementEngine` analiza
el código (módulos grandes, bloques duplicados, TODOs, módulos sin pruebas) y las
señales de runtime (jobs inestables del scheduler) y produce una **lista
priorizada** de mejoras. No cambia nada: recomienda, audita y —si Notion está
habilitado— documenta. Un job semanal la ejecuta.

## ADR-071 · Failover por arriendo de líder, fail-closed a standby

`FailoverCoordinator` implementa HA mínima sin infraestructura externa: varios
nodos comparten un fichero de arriendo; sólo el que sostiene un arriendo fresco
es primario y opera. Si el fichero no se puede leer/escribir, el nodo se queda en
**standby** (nunca asume liderazgo): dos primarios operando a la vez es el peor
resultado posible. Deshabilitado por defecto (requiere un almacén compartido).

## ADR-072 · Update/License/Maintenance preparados: sin auto-deploy ni restricciones

`UpdateManager` compara versión contra un manifiesto, enumera migraciones y
audita aplicaciones/rollbacks, pero **nunca despliega** (eso exige aprobación
humana). `LicenseManager` es estructura para futuras licencias: `is_feature_enabled`
devuelve siempre `True` (sin restricciones, por diseño). `MaintenanceManager`
gestiona la ventana de mantenimiento y limpia temporales antiguos. El CI construye
imágenes pero no despliega: el despliegue a producción es una decisión humana.

# Fase 10 — Quant Research Lab, Auto Strategy Generator y Evolución Autónoma

Laboratorio cuantitativo **independiente de producción** en `app/research/`. Su
objetivo no es operar: descubre, prueba y valida estrategias, y sólo promueve las
mejores —siempre con aprobación humana—. Reutiliza el laboratorio de backtesting
(Fase 6) y el ML (Fase 7); nunca modifica estrategias en producción (trabaja
sobre copias) ni habilita live trading. Fachada `ResearchLab` cableada en
`bootstrap.py`, eventos → Discord (`ResearchNotifier`), rutas `/api/research/*`.

## ADR-073 · El laboratorio es independiente y nunca opera

`ResearchLab` no envía órdenes, no toca posiciones ni el Decision Engine y no
habilita live. Trabaja sobre **copias** (genomas), sus stores son append-only y
la promoción final exige aprobación humana explícita. La regla de oro de las
fases previas (`allow_live=False`, `resolved_mode()→paper`) se mantiene intacta.

## ADR-074 · Genoma declarativo + compilador a `DecisionSource`

Una estrategia experimental es **datos** (`StrategyGenome`: bloques de señal +
filtros de contexto + modo de combinación), no código generado dinámicamente. El
compilador (`compile_genome`) despacha predicados auditados del catálogo y
produce una `DecisionSource` —el mismo contrato de la Fase 6—, así el genoma se
backtestea, optimiza y valida con la infraestructura existente sin `eval`/codegen.

## ADR-075 · Strategy Generator por reglas, no aleatorio

El generador combina bloques siguiendo reglas cuantitativas: **coherencia de
polaridad** (no mezcla seguir-tendencia con reversión en una misma combinación),
**afinidad de filtros** por familia y muestreo reproducible de parámetros dentro
de dominios declarados. Deduplica por firma estructural. Nada de ruido aleatorio.

## ADR-076 · Feature/Factor Lab validados por IC contra el retorno futuro

Cada feature candidata (ATR slope, VWAP distance, delta momentum, liquidity,
trend, book pressure, microprice, spread velocity, volatility expansion) se
valida por cobertura, varianza y **coeficiente de información** contra el retorno
futuro antes de usarse. El Factor Lab investiga siete familias (tendencia,
reversión, liquidez, volatilidad, temporales, volumen, híbridos) y las rankea por
|IC|. Todas las series se calculan sin *lookahead* (usan `candles[: i + 1]`).

## ADR-077 · Optimización multiobjetivo (escalarización + Pareto)

Nunca se optimiza un solo objetivo: eso produce estrategias frágiles. El
`MultiObjectiveOptimizer` envuelve el algoritmo genético de la Fase 6 con una
escalarización ponderada (Profit Factor, Sharpe, Expectancy, SQN, Drawdown…; los
`*_pct` se normalizan /100) y conserva el **frente de Pareto** de las soluciones
no dominadas.

## ADR-078 · Bayesian Lab: TPE sin dependencias + historial comparable

La optimización bayesiana se implementa como un **Tree-structured Parzen
Estimator** puro (sin scikit-optimize/optuna): modela las densidades de los
juegos buenos/malos por cuantil y muestrea donde su razón es mayor. Respeta el
pin numpy<2/scipy y el VPS Bobcat sin SSE4.2. Guarda el historial de cada corrida
y lo compara (`BayesianHistory.compare`) para evidenciar la mejora.

## ADR-079 · Candidate Pipeline sobre `BacktestLab`

Cada genoma pasa, en orden, Backtesting → Walk Forward → Monte Carlo → Validación
ML (asesora, opcional) → Benchmark → Risk Review, orquestados sobre los motores
públicos de la Fase 6 desde **un único backtest** (la curva de equity alimenta la
"estabilidad" y las operaciones alimentan Monte Carlo). Sólo si supera todas las
etapas exigidas se convierte en candidata. El walk-forward usa `random`/`genetic`
(los espacios son rangos continuos; `grid` no aplica).

## ADR-080 · Shadow Mode: evolución segura por evidencia

Una estrategia challenger recibe **los mismos datos** que la vigente, genera
señales y simula operaciones en paralelo —sin órdenes, sin posiciones, sin tocar
el Decision Engine—. Tras un período configurable (mínimo de señales), la prueba
t de Welch sobre las R-múltiples decide si la challenger supera a la vigente de
forma **estadísticamente significativa**. `ShadowSession` cuenta señales en
streaming; `ShadowComparator` emite el veredicto.

## ADR-081 · Promotion Manager fail-closed

La promoción sólo procede si se cumplen **todos** los requisitos: pipeline
superado, validación paper madura (período + evidencia), sin drift por encima del
techo, supera a la vigente por el objetivo compuesto y **el operador aprueba**.
Ante cualquier duda no promueve y registra el motivo. Aunque apruebe, sólo declara
la candidata como promovida a nivel de investigación: el paso a live sigue vetado
por la Fase 9.

## ADR-082 · Conocimiento append-only y stores versionados

Experiment Manager, Knowledge Base, Candidate Store y el historial bayesiano son
**append-only**: cada cambio se anexa y la última versión gana en memoria; nada se
reescribe ni se borra. El conocimiento (qué funcionó, qué falló, por qué, en qué
mercado, con qué parámetros, en qué régimen) nunca se pierde y alimenta las
hipótesis futuras.

## Riesgos conocidos (Fase 10)

- **Datos históricos necesarios**: el pipeline, la optimización y el Shadow Mode
  requieren series de velas; los endpoints del dashboard exponen observación y
  ciclo de vida, pero las corridas pesadas (validar/optimizar/shadow) van por el
  `ResearchEngine`/scripts con un dataset cargado.
- **Coste del walk-forward**: cada fold reoptimiza; en lotes grandes conviene el
  `SimulationCluster` y/o bajar `backtesting.optimizer.max_evaluations`.
- **Order flow aproximado**: delta/CVD/microprice se derivan de
  `buy_volume`/`sell_volume` de la vela; el microprice es un proxy documentado, no
  el bid/ask real. Sin flujo, esos bloques/features se degradan a neutro/NaN.
- **Revisión ML asesora y opcional**: `require_ml_review=False` por defecto (el
  revisor necesita historial de operaciones); nunca decide, sólo aconseja.
- **Live sigue deshabilitado**: el laboratorio descubre y valida; abrir live
  sigue siendo una decisión humana bajo el Live Gate de la Fase 9.

## Riesgos conocidos (Fase 9)

- **Notion sin verificación en vivo**: el backend y la cola están probados con un
  transporte simulado; la creación real de páginas depende de que la base tenga la
  propiedad de título `Name` y de que la integración tenga acceso a ella.
- **Backups del estado en disco, no de PostgreSQL**: cubren lo que el motor
  necesita para no arrancar de cero; el respaldo de la base relacional es
  responsabilidad de `pg_dump`/infra (documentado en el manual de recuperación).
- **Failover básico por fichero**: correcto para un primario + standby sobre un
  volumen compartido; no sustituye a un coordinador de consenso (etcd/Raft) para
  topologías mayores. Deshabilitado por defecto.
- **Improvement/updates heurísticos**: la detección de duplicados/refactor y la
  comprobación de versiones son señales para revisar, no verdades absolutas.
- **Live sigue deshabilitado**: toda esta maquinaria construye el camino a live;
  ninguna pieza lo abre. `allow_live=False` y `resolved_mode()→paper` se mantienen.

## Riesgos conocidos (Fase 8)

- **Aplicación en caliente parcial**: los overrides de config y las intenciones de
  control de estrategias se persisten y auditan, pero su aplicación a un motor ya
  en marcha depende del cableado por subsistema (hoy se leen al arranque/recarga).
- **Backtests por HTTP no ejecutan la operación pesada**: `/api/backtesting/run`
  acepta y audita; los runs reales (candles + `DecisionSource`) siguen siendo
  `BacktestLab`/CLI y aparecen en Experiments. Walk-forward y Monte Carlo, ídem.
- **Logs = buffer de errores**: el visor de logs expone el buffer de errores
  recientes del Health Monitor (no un tail completo de todos los niveles).
- **Verificación en vivo pendiente**: probado en verde (build/tsc/eslint/tests) y
  contra la API en modo degradado; la validación con datos vivos requiere el
  ambiente `paper` (que además emitiría notificaciones Discord reales).

## Riesgos conocidos (Fase 7)

- **Infra preparada, no entrenada aún**: online/incremental learning, uso de
  GPU, entrenamiento paralelo y aprendizaje por refuerzo (bandit) existen como
  estructura; hoy sólo se entrenan los modelos nativos por lotes.
- **Backends pesados no instalados**: XGBoost/LightGBM/CatBoost/redes se saltan
  con nota (por diseño, para el VPS de bajo consumo); el AutoML corre sólo los
  nativos + voting.
- **Etiqueta de estrategia por operación**: `StrategyIntelligence` lee la
  estrategia dominante de `context_snapshot`; hasta que la ejecución rellene ese
  campo, las operaciones se agrupan como `portfolio` (el ranking por estrategia
  queda limitado, no roto).
- **Sin señal direccional por barra**: coherente con la investigación previa, el
  ML no predice dirección; su valor está en filtrar setups y ponderar
  estrategias, no en un modelo de precio.
- **Endpoint de entrenamiento bajo demanda**: `/api/ml/train` dispara un AutoML
  real (operación pesada); en producción conviene reservarlo o encolarlo.

## Riesgos conocidos (Fase 6)

- **Fidelidad intrabar aproximada**: el camino OHLC (ADR-044) no es tick-a-tick;
  un stop y un objetivo alcanzados dentro de la misma vela se resuelven por la
  heurística de dirección, no por el orden real de los ticks.
- **Reloj de replay global**: una corrida por proceso (ADR-043); paralelizar
  backtests exige procesos separados.
- **Bayesiano/Optuna, genético parcial, AutoML**: estructura preparada. Los
  optimizadores bayesiano y Optuna fallan si se invocan; el AutoML declara los
  tipos de modelo pero no entrena (llega en la Fase 7).
- **Reportes PDF y order-book replay**: estructura preparada; hoy se generan
  JSON/Markdown/HTML y se reproduce OHLCV, no el libro nivel-a-nivel.
- **Endpoints del dashboard de solo lectura**: exponen estado/experimentos;
  lanzar backtests u optimizaciones (operaciones pesadas) es por script/CLI.
- **Sin conexión al QuantCore real todavía**: el motor acepta cualquier
  `DecisionSource`; reproducir el pipeline completo de estrategias sobre datos
  históricos (en vez de fuentes deterministas) es el siguiente paso natural.

## Riesgos conocidos (Fase 5)

- **Sin ejecución real todavía**: todas las métricas de paper trading son
  una aproximación de lo que pasaría en real; el objetivo de la fase es
  validar estabilidad prolongada en paper antes de tocar un broker real.
- **Sin criterios estadísticos de go-live definidos aún**: existe la regla
  de "solo tras criterios estadísticos", pero esos criterios concretos
  (umbral de profit factor, muestra mínima, periodo mínimo en paper, etc.)
  todavía no están escritos en ningún documento — pendiente antes de
  siquiera plantear la Fase 6+ de ejecución real.
- **OCO solo preparado, no operativo**: la estructura de orden OCO existe en
  `order_manager/` pero no hay lógica de cancelación cruzada implementada.
- **Ejecución parcial modelada pero no explotada**: `PaperBroker` puede
  simular fills parciales, pero `PositionManager` todavía trata cada
  posición como una unidad; no hay gestión de parciales aguas abajo.
- **3 errores de mypy preexistentes** en
  `tests/unit/test_engine_evaluation.py` (un `make_signal(**dict)` heredado
  de la Fase 4) quedaron fuera de alcance de esta fase — el archivo no se
  tocó.
- **Deuda documental cerrada en esta misma entrada**: `docs/architecture.md`
  y `docs/bitacora.md` no tenían la Fase 5 registrada pese a que
  `docs/execution.md`, el CHANGELOG y el ROADMAP sí la tenían.

## Riesgos conocidos (Fase 4)

- **Sin resultados de trading reales**: la evaluación continua usa
  operaciones virtuales resueltas por velas 1m (sin slippage/fees); es una
  aproximación hasta el paper trading.
- **Order flow con datos públicos**: delta/CVD/imbalance se derivan de
  trades agregados y del libro visible; spoofing/iceberg detection son
  experimentales y de baja confianza por diseño.
- **Decisión por señal**: cada señal admitida dispara una evaluación del
  Decision Engine para su símbolo; con muchísimas estrategias podría
  convenir agrupar disparos (debounce) por símbolo.
- **NewsFilter estático**: las ventanas de noticias vienen de configuración;
  la integración con un calendario económico real queda para más adelante.
- **Drawdown placeholder**: `daily_drawdown_pct` lo fija el runtime state
  (hoy 0.0); lo alimentará la contabilidad real de la fase de ejecución.
- **Parámetros por defecto sin optimizar**: los defaults de las 20
  estrategias son razonables, no calibrados; la calibración llega con
  backtesting + optimizador.

## Próximas tareas (Fase 7+)

- **Machine Learning / IA interna (Fase 7)**: entrenar los modelos que la
  infraestructura AutoML de la Fase 6 deja preparados; aprendizaje continuo,
  autocalificación de estrategias y ajuste dinámico de pesos por activo y
  régimen (plataforma autoevolutiva).
- Conectar el `QuantCore` real como `DecisionSource` del backtest (reproducir
  el pipeline completo de estrategias sobre datos históricos).
- Backfill histórico real (Binance/Bybit/OKX) hacia el Dataset Manager.
- Optimizar los defaults de las 20 estrategias con el laboratorio y versionar
  los conjuntos ganadores; editarlos desde el dashboard.
- Habilitar los optimizadores bayesiano/Optuna y el reporte PDF.
- Adaptador de broker real (MT5/OANDA para XAUUSD) — **solo después** de que
  una estrategia supere el Strategy Qualification Pipeline y acumule evidencia
  en paper; hasta entonces `resolved_mode()` sigue forzando `paper` (ADR-034).

## ADR-083 · Holding mínimo por estrategia, resuelto en tres escalones

**Contexto.** `regime_change_min_holding_seconds` era un valor global. Las
estrategias no comparten el tiempo que su tesis necesita para resolverse
(`order_block` ~1950s vs `bos` ~135s medidos por el evaluador continuo), así que
cualquier valor único es un promedio que perjudica a ambos extremos: corta a las
de tesis larga antes de tiempo (el 80% de las salidas eran por régimen) y deja a
las cortas sin una salida por régimen útil. Medido: +0.26R virtual vs −0.15R real.

**Decisión.** El umbral se resuelve por posición con
`ExecutionSettings.min_holding_seconds_for(strategy, category)`, con fallback en
tres escalones: **valor propio de la estrategia → valor de su categoría → valor
global**. Para que la ejecución pueda resolverlo, la decisión se atribuye a la
estrategia que más aportó al consenso (`Decision.primary_strategy`, determinista
por orden de inserción de las contribuciones) y esa atribución viaja **en el
evento** `DecisionGenerated`, no por una llamada directa: el motor de ejecución
no conoce ni el Decision Engine ni los plugins de `app.strategies`.

**Alternativas descartadas.** (a) Un mapa estrategia→categoría en la ejecución:
duplicaría lo que cada plugin ya declara y se desincronizaría al añadir
estrategias. (b) Inyectar el registro de estrategias en la ejecución: acopla dos
capas que hoy sólo se hablan por eventos.

**Consecuencias.** Una estrategia nueva sin historial no necesita configuración:
hereda el umbral de su categoría, y si tampoco la tiene, el global — es decir, se
comporta exactamente como antes del cambio. El umbral aplicado se persiste en
`context_snapshot.min_holding_seconds` de cada trade, así que la decisión es
auditable a posteriori. El límite global de 4h (`max_holding_minutes`) se sigue
evaluando **antes** y no queda afectado.

## ADR-084 · El evaluador continuo sólo resuelve contra precio posterior a la señal

**Contexto.** `PerformanceTracker.evaluate_open` seleccionaba las velas con
`c.end > opened_at`, lo que incluye la vela **en curso** cuando la señal dispara.
El rango high/low de esa vela contiene precio anterior a la señal, así que una
operación virtual podía "resolverse" contra movimiento que ya había ocurrido. El
síntoma visible fue `choch`: duración media de ~4s (la señal disparaba cerca del
cierre de vela y esa misma vela la resolvía) con una expectativa inflada de
+0.938R. El sesgo afectaba a toda estrategia que dispara tarde dentro de la vela.

**Decisión.** El filtro pasa a `c.start >= opened_at`: sólo velas que empiezan
después de la entrada. Es la única lectura que no mira hacia atrás.

**Consecuencias.** Las métricas del evaluador continuo son ahora comparables con
la ejecución real, que es lo que hace útil el contraste señal-vs-ejecución del
que depende el etiquetado dual del ML. La contrapartida es una resolución algo
más tardía (hasta una vela de retraso) y que **el histórico previo al fix no es
comparable con el posterior**: por eso `choch` no recibe un holding propio hasta
acumular muestra nueva.

## ADR-085 · Los experimentos por estrategia proponen, nunca aplican

**Contexto.** Una estrategia con R negativo persistente necesita una decisión, y
esa decisión no debería depender de que el operador se acuerde de mirar los
números tres días después de un cambio. Pero desactivar automáticamente una
estrategia por una ventana de 72h es exactamente el tipo de automatismo que se
equivoca caro: la muestra puede ser corta, el mercado puede haber cambiado de
régimen, y el cambio que se estaba midiendo puede necesitar más tiempo.

**Decisión.** El sistema automatiza la **medición y el aviso**, no la acción.
Al vencer la fecha de corte emite un veredicto con los números y, si procede,
marca la estrategia como candidata a desactivación y avisa por Discord. El
toggle `execution.strategies_enabled` sólo lo mueve un humano.

Tres salvaguardas más: la ventana se **extiende** en vez de juzgar con muestra
insuficiente; sólo cuentan las operaciones cerradas **dentro** de la ventana (el
experimento juzga bajo las reglas nuevas, no bajo las que se acaban de cambiar);
y el estado se persiste append-only para que un reinicio no reinicie el reloj.

**Consecuencias.** El operador recibe una propuesta accionable con evidencia en
lugar de un recordatorio, y el sistema nunca se apaga una fuente de señal por su
cuenta. El coste es que una estrategia mala sigue operando hasta que un humano
actúa — asumido a propósito: el toggle bloquea sólo la apertura, así que una
estrategia desactivada sigue generando historial y se puede reevaluar.

## ADR-086 · El gobierno del Meta Strategy Manager se aplica por evento y se audita

**Contexto.** El MSM calculaba pesos y activaciones desde la Fase 7 y los
publicaba en el bus, pero el unico suscriptor era el notificador de Discord: el
consenso seguia usando los pesos de configuracion. Ademas, `label_trades` nunca
encontraba la estrategia de origen, asi que el MSM veia una sola entrada
agregada (`portfolio`) en lugar de una por estrategia. El gobierno existia sobre
el papel en los dos extremos: ni entraba evidencia util, ni salia efecto real.

**Decision.** Un servicio dedicado (`MetaGovernanceApplier`) cierra el lazo por
**eventos**, no por una referencia directa: el MSM sigue sin conocer al Strategy
Engine. Su superficie completa son tres metodos de configuracion
(`set_weight`/`enable_strategy`/`disable_strategy`), y **cada cambio efectivo se
registra en el audit log append-only** con el valor anterior, el nuevo y el
motivo.

**Alternativas descartadas.** (a) Que el MSM llamara al Strategy Engine
directamente: acopla la capa de ML al motor de estrategias y rompe la regla de
comunicacion por eventos. (b) Aplicar los pesos dentro del Decision Engine al
leerlos: dejaria el cambio sin auditoria y sin un punto unico donde vetarlo.

**Consecuencias.** El gobierno automatico es real y reversible: `apply_governance`
en `false` deja el aplicador en modo observacion, auditando lo que *habria*
hecho. Un peso que se mueve solo siempre tiene una entrada de auditoria que lo
explica. El aplicador no puede habilitar live porque no conoce ni la ejecucion
ni el Live Gate.

## ADR-087 · Evidencia mixta por estrategia: ejecutada manda, virtual rellena

**Contexto.** Gobernar el peso de una estrategia con 4 operaciones cerradas es
gobernar con ruido. Pero esa misma estrategia puede tener cientos de senales
resueltas por el evaluador continuo, que mide la calidad de la senal *en si*
(TP/SL/timeout puros contra velas futuras, sin sizing ni salidas por regimen).

**Decision.** El score que fija el peso es una mezcla: la evidencia ejecutada
pesa `min(1, trades/min_trades)` y el resto lo aporta la virtual. En cuanto la
muestra ejecutada alcanza el minimo, la virtual deja de influir por completo.
La traza de que evidencia sostuvo cada decision viaja en el informe y en la
auditoria.

**Limite explicito.** La **desactivacion** sigue exigiendo muestra ejecutada. El
rendimiento virtual ignora costes, slippage y salidas por regimen, asi que puede
ser optimista de forma sistematica: sirve para decidir cuanto peso dar a una
estrategia joven, no para apagarla. Hay un test que lo fija.

**Consecuencias.** Una estrategia nueva o poco operada deja de quedarse anclada
en el peso neutro por falta de historial ejecutado, sin que una metrica optimista
pueda apagar nada por si sola. Depende de que el evaluador continuo mida bien —
por eso el fix del ADR-084 es un prerrequisito de este ADR, no un detalle aparte.

## ADR-088 · El training set del ML se segmenta por era de ejecucion

**Contexto.** El ML aprende del historial del propio motor, que arrastra bugs de
ejecucion ya arreglados (stop mal calculado pre-27/07, trailing que apretaba nada
mas abrir pre-29/07, salida por regimen que cortaba la tesis). Entrenar sobre
ellos sin distinguirlos ensena al modelo a penalizar contextos que tenian edge:
la etiqueta dice "operacion mala" cuando la causa fue la ejecucion, no la senal.

**Decision.** Cada operacion se clasifica en una **era de ejecucion** declarada
en configuracion (nombre, fecha de corte, peso, motivo), por su **hora de
entrada**. Las eras cuya medicion es invalida se **excluyen** (peso 0); las de
sesgo acotado se conservan con peso reducido.

**Por que excluir en vez de ponderar a la baja la primera era.** Un stop mal
calculado no produce una muestra ruidosa alrededor del valor correcto: produce un
R sistematicamente equivocado, correlacionado con el propio bug. Bajarle el peso
deja el sesgo dentro, solo que mas callado.

**Por que clasificar por hora de entrada.** Una operacion abierta antes de un fix
corrio bajo las reglas viejas durante casi toda su vida aunque cerrara despues.
Clasificar por salida marcaria como limpias operaciones que no lo son; por
entrada se marca como contaminado algo de mas, que es el error barato.

**Consecuencias.** Anadir la proxima era es configuracion, no codigo. La
procedencia queda auditable en los metadatos del dataset y en `/api/ml/status`.
`ml.data_quality.enabled=false` restaura el comportamiento anterior, para poder
medir el efecto del saneamiento en vez de asumirlo.

## ADR-089 · Dos etiquetas: calidad de senal y calidad de ejecucion

**Contexto.** La etiqueta unica (`win`) mezcla dos preguntas distintas: si la
senal tenia edge, y si la ejecucion capturo ese edge. Una operacion cerrada por
cambio de regimen o por tiempo **nunca llego a poner a prueba su tesis**, asi que
contarla como "senal mala" ensena al modelo justo lo contrario de lo que ocurrio.

**Decision.** Dos etiquetas explicitas. `win`/`rr_positive`/`not_stopped` miden
la **ejecucion** y usan todas las salidas. `signal_quality` mide la **senal** y
usa solo las operaciones cuyo cierre resolvio la tesis (objetivo, stop, trailing,
break-even). Cada dataset declara que mide en `metadata["label_measures"]`.

**Limitacion asumida.** La fuente ideal para `signal_quality` es el evaluador
continuo (TP/SL/timeout puros contra velas futuras, sin ejecucion), pero hoy no
hay clave de union fila a fila: el evaluador agrega por estrategia y el
`TradeRecord` no lleva los `signal_id` de origen. La aproximacion desde el
journal es honesta y sin lookahead, pero sigue midiendo operaciones ejecutadas,
con sus costes. El cierre del hueco esta documentado en `docs/ml.md`.

**Consecuencias.** Se puede entrenar y comparar los dos objetivos por separado, y
la divergencia entre ambos es en si misma la metrica que interesa: mide cuanto
edge esta perdiendo la ejecucion.

## ADR-090 · Un modelo caduca cuando cambian las reglas de ejecucion, no solo cuando empeora

**Contexto.** La puerta de validacion mide si un modelo es bueno *sobre los datos
con los que se entreno*. No dice nada sobre si esos datos siguen describiendo
como opera el motor. Cambiar el holding minimo, el sizing, un filtro de riesgo o
el trailing altera la distribucion de operaciones que el motor genera, pero
**las metricas del modelo activo no se mueven**: siguen siendo las del dia en que
se valido. El modelo caduca en silencio.

**Decision.** Se congela junto a cada modelo un **hash de las reglas de ejecucion
significativas** y se compara periodicamente con las vigentes. Al diferir, el
modelo se marca como "requiere reentrenamiento" y se avisa por Discord indicando
que familia de reglas cambio.

**Que entra en la huella y que no.** Entran las reglas que cambian *que
operaciones existen y como se gestionan* (holding, trailing, sizing, filtros de
riesgo, toggles de simbolo/estrategia). Quedan fuera cadencias, rutas e
intervalos de reporte: una alerta que salta por cambios cosmeticos ensena al
operador a ignorarla, y una alerta ignorada es peor que ninguna.

**Por que no reentrenar automaticamente.** Porque el cambio de regla que dispara
la alerta es justo el momento en que **todavia no existen datos bajo las reglas
nuevas**. Reentrenar en ese instante produciria un modelo entrenado sobre el
regimen viejo con la etiqueta de estar al dia — peor que saber que esta obsoleto.
La decision de cuando reentrenar necesita un humano que sepa cuanta muestra nueva
hay. Coherente con la regla de la fase: el ML asesora, nunca decide por si solo.

**Consecuencias.** Un estado `unknown` distingue "no consta" de "obsoleto" para
los modelos anteriores a este control, en vez de dar una falsa tranquilidad.
`RULES_VERSION` permite invalidar todas las huellas a proposito cuando cambie el
propio criterio de que es significativo.

## Sizing y limites de exposicion frente al crecimiento del capital

> **Por que esta escrito aqui:** estos limites estan calibrados para un equity
> concreto y pequeno. Si la cuenta crece y nadie los revisa, se quedan pegados —
> y el modo de fallo no es un error, es riesgo silencioso.

### Calibracion actual

Los topes vigentes (`execution.risk.max_exposure_pct` y companeros) estan
pensados para **~$200-500 de equity con leverage 2000:1**. Con ese capital, un
`max_exposure_pct` del orden de 2000 % no es agresivo: es lo minimo para que el
**lote minimo del broker quepa** en la cuenta. El oro, por ejemplo, necesita un
nocional que con un tope del 20 % exigiria ~20k de equity.

Es decir: el tope alto no expresa apetito de riesgo, expresa una **restriccion de
granularidad del broker**. Esa es la razon por la que puede envejecer mal — deja
de ser necesario mucho antes de dejar de estar configurado.

### Cuando hay que revisarlos

| Equity | Que revisar |
| --- | --- |
| **~$1 000** | Primer aviso. `max_exposure_pct` empieza a ser holgura real y no necesidad. Comprobar si el lote minimo de cada simbolo ya cabe con topes normales. |
| **~$2 000-5 000** | `max_exposure_pct` deberia bajar hacia valores convencionales (100-300 %). Revisar `max_symbol_exposure_pct` y `max_correlation_exposure_pct`, que con equity pequeno casi nunca se activaban. |
| **~$20 000** | El oro cabe con un tope del 20 %. A partir de aqui los topes deberian estar dominados por criterio de riesgo, no por granularidad del broker. Revisar tambien `risk_per_trade_pct`, calibrado con la misma logica. |

### Senal de que hay que actuar

Si `max_exposure_pct` sigue en miles cuando el equity ya permite operar con topes
convencionales, el sistema esta autorizando una exposicion que **ya no necesita**.
Los limites siguen "funcionando" —no saltan errores— y por eso el problema no se
manifiesta hasta que un movimiento adverso lo revela.

Los cuatro parametros a revisar juntos, porque se calibraron juntos:
`execution.risk.max_exposure_pct`, `execution.risk.max_symbol_exposure_pct`,
`execution.risk.max_correlation_exposure_pct` y
`execution.sizing.risk_per_trade_pct`.

Todos estan en la whitelist del Config Center, asi que se pueden ajustar sin
reinicio — pero **no hay nada automatico que avise**: es una revision manual
ligada a hitos de capital, y por eso queda escrita aqui.

## ADR-091 · El reloj inyectable vive en un ContextVar, no en un global

**Contexto.** `utc_now()` es un seam para que el backtesting reproduzca el tiempo
historico sin duplicar el motor de ejecucion (ADR de la Fase 6). El proveedor se
guardaba en un **global de modulo**. Pero el `BacktestLab` corre en el **mismo
proceso y el mismo event loop** que el motor en vivo, asi que ese global no
distingue quien pregunta la hora.

Consecuencia observada en produccion (2026-07-31 → 2026-08-04): un backtest
instalo el reloj simulado, su bloque nunca se cerro, y el motor vivio 4 dias
creyendo que era el 31 de julio. El validador de mercado veia todos los ticks del
broker "en el futuro" y descartaba el 100%: sin velas, sin señales, sin
operaciones. El proceso seguia vivo y respondiendo, asi que nada aviso.

Y el problema no era solo la fuga: **aunque el bloque cerrase correctamente, el
motor en vivo veia la hora simulada durante toda la ejecucion del backtest**.

**Decision.** El proveedor pasa a `ContextVar`. El alcance del reloj simulado
queda limitado a la tarea que lo instala y a las que ella crea — exactamente el
alcance de un backtest, que es lo que se queria — y las tareas del motor en vivo,
creadas en otro contexto, ven siempre el reloj de pared.

**Alternativas descartadas.** (a) Correr los backtests en otro proceso: es la
solucion mas fuerte y sigue siendo deseable a futuro, pero es un cambio de
arquitectura mucho mayor y no habria arreglado la fuga de hoy. (b) Un `try/finally`
mas defensivo: no resuelve nada, el `finally` ya existia — el bloque simplemente
nunca llego a ejecutarse.

**Consecuencias.** El backtesting mantiene su semantica (sus propias tareas
heredan el contexto y ven la hora simulada; hay test que lo fija). `wall_now()` y
`clock_skew_seconds()` permiten **medir** la desviacion, que es lo que faltaba
para que el fallo fuese visible.

## ADR-092 · La desconexion de un WebSocket se detecta leyendo, no escribiendo

**Contexto.** El endpoint `/ws/events` empujaba eventos en un `while True` y solo
salia con `WebSocketDisconnect`. Cuando un cliente desaparece **sin cierre
limpio**, `send_json` no lanza: asyncio marca el transporte con `_conn_lost`,
descarta el envio y vuelve. El bucle seguia consumiendo eventos y "enviandolos"
a un socket muerto indefinidamente, y la suscripcion al bus nunca se liberaba.

En produccion eso fue el **75% de todas las lineas de log** (`socket.send()
raised exception.`, que CPython emite a partir del quinto intento sobre un
transporte perdido) y un suscriptor zombi por cada recarga del dashboard.

**Decision.** El envio va en una tarea aparte y la corrutina del endpoint espera
en `websocket.receive()`. En un canal de solo lectura para el cliente, `receive`
solo retorna cuando llega el `websocket.disconnect`: es la unica señal fiable.

**Consecuencias.** La limpieza de la suscripcion deja de depender de que el envio
falle — que es justo lo que no ocurria. Escribir sobre un socket muerto ya no
puede convertirse en un bucle infinito silencioso.

## ADR-093 · Vigilar el efecto (ciego / mudo), no solo las causas conocidas

**Contexto.** El motor estuvo 4 dias sin operar por un reloj simulado filtrado, y
**ninguna comprobacion de salud lo detecto**: el proceso respondia, los servicios
estaban `running` y el watchdog de componentes no veia nada. Se añadio
`clock_skew_seconds` para esa causa concreta, pero la leccion es mas amplia: la
salud se medía por *señales de vida del proceso*, no por *si el motor estaba
haciendo su trabajo*.

**Decision.** Dos alarmas sobre el **efecto observable**, independientes de la
causa: **ciego** (se descarta >=95% de los datos entrantes) y **mudo** (entran
datos limpios y no sale ninguna señal durante N ventanas). Cualquier fallo que
deje al motor sin operar en silencio cae en una de las dos.

**Por que por deltas y no por acumulados.** Un ratio acumulado diluye el presente
y, tras un incidente largo, seguiria marcando rojo mucho despues de haberse
recuperado — precisamente cuando hace falta saber que ya esta bien.

**Por que "mudo" exige datos limpios en vez de un calendario de sesiones.** El
flujo de datos es evidencia directa de que el mercado esta vivo; un calendario
hay que mantenerlo, se equivoca en festivos y en horarios especiales, y una
alarma que salta cada noche con el mercado cerrado se acaba ignorando.

**Consecuencias.** El sistema deja de depender de que alguien mire el dashboard
para enterarse de que no esta operando. El coste es una fuente mas de alertas, y
por eso ambas llevan latch y aviso de recuperacion: la utilidad de una alarma es
inversamente proporcional a cuantas veces se repite sin novedad.

## ADR-094 · El `signal_id` viaja como campo del evento, no como acoplamiento

**Contexto.** El Bloque 4 dejó declarada su limitacion mas importante: el ML
aproximaba la calidad de la senal filtrando el Trade Journal **por motivo de
salida**, porque no habia forma de comparar senal a senal contra el resultado
que calcula el evaluador continuo. Faltaban dos piezas: el evaluador agregaba
por estrategia y descartaba el resultado individual, y el `TradeRecord` llevaba
`decision_id` pero no los `signal_id` que lo originaron.

La consecuencia no era academica. La aproximacion **descarta justo las
operaciones que la ejecucion corto** (regimen, tiempo, kill switch), que son
precisamente las que separan "la senal no tenia edge" de "lo tenia y la
ejecucion no lo capturo" — la pregunta que abrio toda esta linea de trabajo el
2026-07-29.

**Decision.** Dos cambios, ninguno de ellos un acoplamiento nuevo:

1. `Decision.signals_considered` (que ya existia) se publica en
   `DecisionGenerated.signal_ids` y se arrastra por `OrderRequest` → `Position`
   → `TradeRecord`. Es **el mismo patron exacto** que el Bloque 1 uso para
   `strategy`/`strategy_category`: un campo del evento, no una llamada entre
   modulos. La ejecucion sigue sin conocer el Decision Engine ni los plugins.
2. El evaluador continuo persiste el resultado **por senal** en un store
   append-only (`VirtualOutcomeStore`), en paralelo al agregado por estrategia
   que ya publicaba. Se anade una salida; no se sustituye ninguna.

El join vive en la capa de datasets del ML (`app/ml/datasets/join.py`) y declara
su propio `SignalOutcome`, adaptado en el composition root — misma regla que
`VirtualStrategyStats` en ADR-087: el ML no depende de `app.engine.evaluation`.

**Los casos sin match se cuentan, no se esconden.** Son tres y no significan lo
mismo: `unmatched_legacy` (journal anterior al bloque y posiciones adoptadas del
broker — cae al camino antiguo, porque hoy es casi todo el historial),
`unmatched_unresolved` (senal sin niveles o virtual aun abierta — fuera de la
etiqueta de senal, dentro de las de ejecucion) y `signal_without_trade` (senal
filtrada o vetada por riesgo — no es fila del dataset, pero es la evidencia mas
limpia que existe sobre esa senal). Van a `metadata["join_breakdown"]`, con el
mismo estandar de procedencia auditable que el `era_breakdown` del Bloque 4.

**Alternativa descartada.** Reconstruir el vinculo a posteriori cruzando
`decision_id` con el historial de decisiones persistido: es posible, pero exige
que la DB este viva y disponible en el momento del entrenamiento, y no cubre las
decisiones purgadas. Un campo en el registro es mas barato y no depende de nada.

**Consecuencias.** El saneamiento por era se aplica **despues** del join: el
join cambia como se etiqueta una operacion, no cuales son medibles. La
aproximacion por motivo de salida sobrevive como fallback y seguira siendo el
camino mayoritario hasta que el journal post-bloque acumule muestra — el propio
`labelled_from_join` lo hace visible en vez de dejarlo implicito.

Efecto colateral cerrado de paso: el snapshot de recuperacion no persistia
`strategy`/`strategy_category` (hueco del Bloque 1). Una posicion restaurada
tras un reinicio perdia su atribucion, caia al holding **global** en vez del
suyo por estrategia, y su operacion llegaba al journal sin nada que unir.

## Riesgos de aislamiento de procesos (Bloque 11)

El incidente del reloj (ADR-091) no fue un bug aislado: fue el sintoma de que el
backtesting, el research y el motor en vivo comparten **proceso, event loop y
espacio de modulos**. Esta seccion audita que otro estado compartido podria
producir la misma clase de fallo.

### Auditoria de estado compartido

| Sitio | Patron | Veredicto |
| --- | --- | --- |
| `app/utils/time.py` | proveedor de reloj | **Cerrado** (ADR-091): `ContextVar`, alcance = la tarea del backtest. |
| `app/backtesting/quant_source.py` `_LOOP` | event loop en hilo daemon, global de modulo, creado por el backtesting | **Riesgo real, distinto del reloj.** No corrompe estado del motor —el motor en vivo nunca usa ese loop—, pero es un hilo que se crea y **nunca se cierra**: sobrevive al backtest que lo creo y a cualquier numero de corridas posteriores. Su presencia es evidencia de que un backtest corrio en el proceso, y por eso el guard de arranque la usa como senal. |
| `app/logging/recent.py` `_buffer` | singleton de modulo con `global` | **Aceptable.** Se instala una vez y es de solo-anadir; un backtest no puede corromperlo, como mucho mete ruido en el buffer de errores. Mismo patron, consecuencia distinta. |
| `app/engine/signal_engine/engine.py` `_BACKGROUND` | `set` de tareas a nivel de modulo | **Aceptable.** Es un anclaje contra la recoleccion de basura de tareas (`add_done_callback(discard)`), no estado de negocio. Un `SignalEngine` de backtest comparte el `set` con el del motor, pero las tareas son independientes. |
| `app/brokers/mt5/connection.py` | `MetaTrader5` es singleton **de proceso** | **Riesgo inherente, no del codigo.** Lo impone la libreria: un backtest y el motor en vivo comparten forzosamente el mismo terminal. No se puede aislar sin separar procesos — es el argumento mas fuerte a favor del punto siguiente. |

**Lo que se comprobo y NO era un problema.** `QuantCoreDecisionSource` ejecuta su
pipeline en el hilo del `_LOOP` via `run_coroutine_threadsafe`, lo que hacia
sospechar que el reloj simulado no llegaria hasta alli (y que los backtests
estarian usando hora de pared en silencio). Se verifico empiricamente: sí llega
— `call_soon_threadsafe` copia el contexto del hilo llamante. Queda escrito para
no repetir la sospecha.

**Lo que esta auditoria no puede cubrir.** Los monkeypatch de un test no se
pueden enumerar en tiempo de ejecucion. Por eso el guard no busca parches
concretos, sino el **entorno que los produce** (`pytest` cargado,
`PYTEST_CURRENT_TEST` definida): detectar la causa es posible, el efecto no.

### Guard de arranque (implementado)

`app/engine/startup_guard.py`, invocado al principio de `QuantEngine.start()`.
Tres comprobaciones: desviacion del reloj, instrumentacion de test cargada, y
event loop de backtesting ya activo. Ver ADR-095.

### Aislamiento por procesos (propuesta, NO implementada)

Tres opciones, de menor a mayor coste:

1. **Guard de arranque** — hecho. No aisla nada; solo impide operar contaminado.
2. **Backtest/research en proceso hijo** (`multiprocessing`, frontera = Event
   Bus o cola). Elimina de raiz el reloj compartido, el `_LOOP`, los modulos
   compartidos y la contencion del terminal MT5. Coste: serializar los datos de
   entrada del backtest y el resultado; el `BacktestLab` hoy recibe objetos
   vivos (`MarketDataService`, `DecisionSource`), asi que no es un cambio
   trivial. **Es la opcion recomendada** si se va a activar `auto_cycle`.
3. **Contenedores separados** — el aislamiento mas fuerte, pero arrastra la
   decision de plataforma de abajo.

### Linux + Docker Compose vs Windows + Tarea Programada

La pregunta del bloque es si migrar `qevps` habria cortado el incidente. **No:
el incidente fue estado compartido dentro de un unico proceso Python, y eso
ocurre igual en Linux, en Docker y en Windows.** Docker aisla procesos entre
contenedores, no dentro de uno.

Dicho eso, la comparacion en si:

| | Windows + Tarea Programada (hoy) | Linux + Docker Compose |
| --- | --- | --- |
| MT5 | Nativo. **Unica plataforma soportada por la libreria.** | Requiere Wine, o cambiar de broker. |
| Aislamiento | Un proceso para todo. | Un contenedor por servicio, limites de CPU/memoria reales. |
| Reproducibilidad | Depende del estado de la maquina (el `w32time` parado del 04/08 lo ilustra). | Imagen versionada. |
| Coste de migracion | — | Alto, y **dominado por MT5**, no por el resto. |

**Recomendacion: no migrar ahora.** La dependencia de MT5 con Windows convierte
la migracion en un cambio de broker encubierto, que es una decision de capital y
no una decision tecnica. El beneficio que se buscaba —aislar backtest del motor—
se consigue mas barato con la opcion 2 (proceso hijo), que no toca la
plataforma. Reevaluar si algun dia se deja de depender de MT5.

## ADR-095 · El motor no arranca con instrumentacion de test o backtest activa

**Contexto.** El motor estuvo 4 dias operando con el reloj de un backtest
instalado. ADR-091 impide esa fuga concreta y ADR-093 vigila el efecto (ciego /
mudo), pero faltaba comprobar el estado del proceso **en el momento de
arrancar**: las dos anteriores actuan durante la operacion, no antes de ella.

**Decision.** Un guard fail-fast al principio de `QuantEngine.start()`, con tres
comprobaciones: desviacion del reloj efectivo frente al de pared, presencia de
instrumentacion de test (`pytest` en `sys.modules`, `PYTEST_CURRENT_TEST`), y un
event loop de backtesting ya activo en el proceso.

**Por que abortar y no degradar.** Un warning habria devuelto exactamente el
modo de fallo del incidente: operar mal, en silencio, mientras todo figura
`running`. Un motor que no arranca se detecta en el primer minuto; uno ciego
tardo cuatro dias. La asimetria de coste es el argumento entero.

**Por que solo en `paper` y `production`.** En `development` y `testing` la
instrumentacion de test es lo esperado — de hecho la propia suite arranca el
motor. Abortar alli convertiria al guard en un estorbo, y un guard que estorba
se acaba desactivando: es asi como se pierden las salvaguardas. En esos entornos
informa y no bloquea, y hay un test que fija ambas mitades del contrato.

**Lo que el guard NO hace.** No aisla nada ni sustituye a separar procesos
(propuesta arriba, sin implementar). Detecta que el proceso viene sucio; no
impide que se ensucie. Tampoco puede enumerar monkeypatch: detecta el entorno
que los produce, no los parches.

**Consecuencias.** Hay un test explicito de que un arranque limpio **no** se
bloquea, tan importante como los de deteccion: el fallo esperable de un guard
como este es el falso positivo que obliga a desactivarlo.

## ADR-096 · El ciclo autonomo se apaga solo si degrada al motor, y no se rearma

**Contexto.** El Bloque 6 puso techo al ciclo autonomo del Research Lab
(ventana, topes, timeout, vetos de CPU y posiciones abiertas), pero todo eso
decide **si el ciclo puede arrancar**. Nada decidia si, una vez corriendo, habia
que **apagarlo**. La diferencia importa: el peor caso del presupuesto es
posponer un ciclo; el peor caso de no vigilar el efecto es haber estado
degradando la operativa sin que nadie lo notase — que es literalmente la
leccion del incidente del reloj.

**Decision.** Un vigilante (`app/research/rollback.py`) evaluado antes de cada
ciclo. Cuatro disparadores: CPU sostenida, latencia del bucle de gestion de
posiciones, desviacion del reloj y alarmas de pipeline (ciego/mudo). Al
disparar, `auto_cycle` pasa a `false`, se publica `ResearchCycleRolledBack` y
Discord avisa.

**CPU sostenida, no un pico.** Un pico de CPU durante un ciclo de research es
*exactamente lo esperado*: disparar con el primero apagaria la vigilancia en el
primer ciclo que hiciera su trabajo. Se exigen 3 muestras consecutivas, y una
racha rota vuelve a cero.

**La latencia se juzga contra su propia referencia, no contra un absoluto.** Lo
que delata la competencia por CPU es la **degradacion relativa**: un bucle
establemente lento no es culpa del research, uno que se duplica si — aunque siga
siendo rapido en terminos absolutos. Para medirlo hubo que instrumentar el bucle
de gestion (`ExecutionEngine.manage_latency`), que hasta ahora **no se media**:
no habia forma de saber si algo le estaba robando CPU al unico bucle que no
puede llegar tarde.

**Con muestra escasa no se juzga.** Menos de 30 pasadas, o sin linea base, no
dispara. Comparar contra una referencia que no existe es como se fabrican los
falsos positivos que acaban con la vigilancia desactivada.

**Asimetria deliberada: solo apaga el laboratorio.** Nunca toca la operativa, no
cierra posiciones y no puede habilitar live. Ante la duda, el que se sacrifica
es el research — cuesta un ciclo de generacion, no dinero.

**No se rearma solo.** Reactivar es una decision humana. Un rollback reversible
automaticamente convertiria un problema persistente en un ciclo de
encendido/apagado, mas dificil de diagnosticar que el fallo original.

**El apagado es en memoria, no toca el `.env`.** El job lee `auto_cycle` en cada
disparo, asi que basta para que no vuelva a entrar; persistirlo seria que el
codigo se reescriba la configuracion del operador.

**Consecuencias.** El plan de activacion gradual (tres fases, en
`docs/research.md`) y estos umbrales son la misma cosa: hay un test que exige
que los numeros documentados sean los configurados, porque un plan que diverge
del codigo no vale nada. `auto_cycle` sigue en `false`, con su test.

## ADR-097 · Criterios de graduacion a live: escritos, medidos, y sin poder activar nada

**Contexto.** Desde la Fase 5 existe la regla *"solo se habilita live tras
cumplir criterios estadisticos"*, y desde la Fase 5 esta anotado como riesgo
conocido que **esos criterios no estaban escritos en ningun sitio**. Una regla
sin umbrales no es un control: no se puede incumplir porque no se puede evaluar.

**Decision.** Siete criterios en `app/production/live/graduation.py`, con el
razonamiento de cada umbral en su propio docstring, y una herramienta
(`scripts/graduation_gap.py`) que los mide contra un Trade Journal real.

| Criterio | Umbral | Por que |
| --- | --- | --- |
| Muestra | >= 400 operaciones | Con 100 y expectativa de +0.1R el error estandar tapa el resultado: no se distingue edge de suerte. |
| Expectativa | >= +0.10R | Positiva **con margen**: exigir >0 aprobaria un sistema que empata, y en real empatar es perder (el paper no cobra swaps ni sufre requotes). |
| Profit factor | >= 1.30 | Por debajo de ~1.2 el resultado lo domina el ruido. |
| Drawdown maximo | <= 15 % | Sobre equity pico. |
| Dias en paper | >= 60 | El calendario importa **aparte** de la muestra: 400 operaciones en tres dias miden un unico momento de mercado con mucho detalle. |
| Cobertura de regimenes | >= 3, con >= 30 operaciones cada uno | Cinco operaciones en `trending` no son cobertura de `trending`. |
| Salidas forzadas | <= 50 % | **Criterio anadido por los datos, no por el enunciado.** Ver abajo. |

**El criterio que no estaba pedido y es el mas importante.** Si el motor cierra
la mayoria de sus posiciones por regimen, tiempo o kill switch, sus estrategias
**casi nunca llegan a poner a prueba su propia tesis**. Un sistema asi puede
tener expectativa positiva y no haber demostrado nada sobre sus senales: lo que
se graduaria a real seria la ejecucion, no la estrategia. En el journal medido
esto esta al 75.6 % — es el hallazgo que justifica el criterio.

**Cumplirlos no activa nada.** El modulo no importa el `LiveGate`, no toca
`allow_live` y no tiene ningun camino hacia `resolved_mode()`. Hay un test que
construye un historial que cumple **los siete** criterios y comprueba que
despues `resolved_mode()` sigue devolviendo `paper` y `allow_live` sigue en
`False`. El informe lo dice ademas por escrito en su propio `to_dict()`: un
informe que parece una aprobacion acabaria usandose como tal.

**Consecuencias.** El gap medido (abajo, y en la bitacora) dice que el sistema
no esta cerca — y no en una direccion que el tiempo arregle solo. Ese es el
valor del ADR: convierte "todavia no" en un numero.

## Hitos de capital: la formula, la tabla y el checklist (Bloque 13)

La seccion "Sizing y limites de exposicion frente al crecimiento del capital"
(arriba) ya explicaba **por que** los topes envejecen mal: expresan una
restriccion de granularidad del broker, no apetito de riesgo. Lo que le faltaba
—y es lo que pide este bloque— es la **formula** detras de cada hito. Sin ella
los umbrales son numeros afirmados, y hay que reinvestigarlos cada vez que
cambie el broker, el simbolo o el precio.

### La formula

El tope de exposicion no puede bajar de lo que ocupa **una sola posicion del
lote minimo**:

```
nocional_lote_minimo = volume_min x contract_size x precio

max_exposure_pct minimo viable = nocional_lote_minimo / equity x 100

equity necesario para un tope dado = nocional_lote_minimo / (tope / 100)
```

Todo el envejecimiento de estos limites sale de ahi: el numerador lo fija el
broker y el mercado, el denominador crece con la cuenta. **El tope no deja de
ser necesario poco a poco: deja de serlo en un punto concreto y calculable.**

### Nocional del lote minimo, por simbolo (demo Exness)

| Simbolo | `contract_size` | Lote min. | Nocional lote min. |
| --- | --- | --- | --- |
| ETHUSDm | 1 | 0.1 | ~$195 |
| USTECm | 1 | 0.01 | ~$281 |
| BTCUSDm | 1 | 0.01 | ~$650 |
| XAUUSDm | **100** | 0.01 (= 1 oz) | ~$4.083 |

XAUUSDm tiene `contract_size=100`: su lote minimo es **6 veces** el de BTC y 21
veces el de ETH. Por eso esta con el toggle en OFF, y no por una decision de
riesgo.

### Qué exposicion ocupa una sola posicion, segun el equity

| Simbolo | @$200 | @$1.000 | @$5.000 | @$20.000 |
| --- | --- | --- | --- | --- |
| ETHUSDm | 97 % | 19 % | 4 % | 1 % |
| USTECm | 140 % | 28 % | 6 % | 1 % |
| BTCUSDm | 325 % | 65 % | 13 % | 3 % |
| XAUUSDm | 2.041 % | 408 % | 82 % | **20 %** |

Esta tabla **deriva** los hitos que antes estaban afirmados. El "~$20k para el
oro" no era una intuicion: es exactamente $4.083 / 0.20 = **$20.414**.

### Equity necesario para operar bajo un tope convencional

| Tope | ETHUSDm | USTECm | BTCUSDm | XAUUSDm |
| --- | --- | --- | --- | --- |
| 300 % | $65 | $94 | $217 | $1.361 |
| 100 % | $195 | $281 | $650 | $4.083 |
| 20 % | $974 | $1.403 | $3.248 | $20.414 |

### Checklist: que revisar cuando suba el capital

Los cuatro parametros se calibraron juntos y se revisan juntos. Al alcanzar cada
hito, recalcular con la formula de arriba (los precios cambian; la formula no):

**~$1.000**
- [ ] `max_exposure_pct`: con ETH y USTEC ya por debajo del 30 %, los 2000 % son
      holgura pura. Bajar hacia 300-400 %.
- [ ] `max_symbol_exposure_pct` (hoy 400 %): empieza a poder ser un control real
      y no un estorbo.
- [ ] Comprobar si el lote minimo de cada simbolo cabe con topes normales
      (formula: `nocional_lote_minimo / equity`).
- [ ] El oro **sigue fuera** (408 % de exposicion por posicion).

**~$2.000-5.000**
- [ ] `max_exposure_pct` hacia valores convencionales (100-300 %).
- [ ] `max_correlation_exposure_pct` (hoy 800 %): con equity pequeno casi nunca
      se activaba, asi que **nunca se ha probado de verdad**. Revisar que el
      grupo de correlacion cripto (BTC+ETH) este bien definido antes de que el
      limite empiece a morder.
- [ ] BTC deja de estar "al filo" del 0,5 % de riesgo por operacion.
- [ ] Reevaluar el oro a partir de ~$4.083 (100 % por posicion) — todavia alto.

**~$20.000**
- [ ] El oro cabe con un tope del 20 %: reactivar `XAUUSDM` en
      `execution.symbols_enabled` **es una decision, no un automatismo**.
- [ ] Los topes deberian estar dominados por criterio de riesgo, no por
      granularidad del broker. Es el momento de fijarlos por apetito real.
- [ ] `risk_per_trade_pct` (0,5 %): calibrado con la misma logica de
      granularidad, revisar con el mismo criterio.
- [ ] `max_position_pct` (hoy 400 %) baja a un valor convencional.

Los cuatro estan en la whitelist del Config Center (aplican en caliente), pero
**nada avisa automaticamente**: es una revision manual ligada a hitos, y por eso
esta escrita aqui.

### Universo de simbolos: que ganaria un exchange nativo

Hoy el universo operable son **4 simbolos**, y no por eleccion: Exness demo
deshabilita las altcoins (`trade_mode=0` en SOL/ADA/DOGE/LTC/XRP/BNB), descarta
`BTCUSDTm` y `USTEC_x100m` por falta de cotizacion, y `ETHBTCm` por
`contract_size=100`. De los 4 que quedan, uno (oro) esta apagado por tamano de
contrato. **Operativa real: 3 simbolos, dos de ellos altamente correlacionados
(BTC y ETH).**

Que cambiaria con el feed/ejecucion nativa de Binance o Bybit (enlaza con el
Bloque 9):

- **Diversificacion real.** Decenas de pares con liquidez suficiente, y
  altcoins con regimenes que no son el de BTC. Hoy `max_correlation_exposure_pct`
  es casi decorativo porque **el universo entero esta correlacionado**.
- **Granularidad mucho mejor.** El lote minimo de un exchange spot es de
  ordenes de magnitud menor que el de un CFD: el problema de este bloque entero
  —topes dictados por granularidad— practicamente desaparece.
- **El oro no aplica**: no hay XAU en un exchange cripto. Seguiria necesitando
  el bróker CFD, o quedarse fuera.

**No es una recomendacion de cambiar de bróker** — eso es una decision de
capital, y ademas seria prematura mientras la expectativa siga siendo negativa
(ADR-097). Es el dato para cuando esa decision se plantee: el techo de
diversificacion actual **no es del motor, es del bróker**.

## Riesgo conocido: el order flow no esta aproximado, esta ausente (Bloque 9)

Los riesgos de Fase 4 y Fase 10 describian el order flow como "aproximado" y las
heuristicas de spoofing/iceberg como "experimentales". **La auditoria del Bloque
9 corrige ese diagnostico a peor**, y conviene que quede escrito con precision:

- `app/market/providers/mt5.py:39` — las capacidades del proveedor MT5 son
  `{TICKER, TRADES, CANDLES}`: **no incluye `ORDERBOOK`**. Y aunque `TRADES`
  figura, el bucle de polling solo emite `Ticker`, nunca `Trade`. Resultado:
  `get_recent_trades()` devuelve vacio para todo simbolo de MT5.
- Por tanto `imbalance`, `book_pressure`, `spoofing_score`, `iceberg_score` y
  `consumption` son `None`/`0` en produccion, y `delta`/`CVD`/`aggression`/
  `absorption`/`exhaustion` se calculan sobre una lista vacia. No es una
  aproximacion de baja calidad: **es la ausencia del dato**.
- El `volume` de las velas MT5 es `tick_volume` (numero de cambios de precio),
  y el campo `trades` reutiliza ese mismo numero. No es volumen negociado.

**Confirmacion empirica.** En 1209 operaciones reales de produccion no hay **ni
una** atribuida a `delta`, `cvd` u `order_book_imbalance`. Las tres estrategias
puras de order flow nunca han disparado. Las que si operan son SMC estructural
(`fair_value_gap`, `bos`, `mss`, `choch`, `order_block`), que se calcula sobre
OHLC y **no depende del libro** — de ahi que esas si funcionen.

Diagnostico afinado: **SMC estructural esta bien servido por MT5; el order flow
no esta servido en absoluto.**

**Desincronizacion CFD ↔ spot, medida** (294 operaciones de BTC y 464 de ETH
contra Binance): el CFD cotiza sistematicamente ~10 bps por debajo del spot,
con desviacion mediana ~10 bps y p95 de 18-20 bps. Que la mediana coincida con
el sesgo medio indica **offset estable, no ruido**. Irrelevante para senales de
order flow (son diferenciales); **relevante para niveles y ejecucion**, que
deben seguir usando siempre el precio del broker.

**Recomendacion (informe completo en `docs/orderflow_nativo.md`): no es
prioritario migrar la fuente.** No porque el dato aproximado baste, sino porque
anadir una familia de estrategias nueva a un motor con expectativa negativa
(ADR-097) y con el 75% de las salidas decididas por la ejecucion es optimizar en
el orden equivocado. Lo que si conviene ya, y es gratis: desactivar las tres
estrategias de order flow mientras la fuente sea MT5 — una estrategia inerte que
figura como activa es deuda de honestidad.

## Deriva entre el entorno de desarrollo y produccion (medido 2026-08-04)

El pendiente heredado decia *"`requirements.txt` pinea `redis>=5.0,<6.0` pero el
venv tiene 8.0.0"*, sugiriendo que el pin estaba obsoleto. **Comprobado contra
`qevps`: es al reves.**

| | `qevps` (produccion) | Entorno de desarrollo |
| --- | --- | --- |
| `redis-py` | **5.3.1** (respeta el pin) | 8.0.0 |
| Python | **3.12.10** | 3.14.6 |

El pin no esta desalineado: **el entorno de desarrollo se desvio del pin.**
Subir el techo a 8.x habria cambiado el cliente de Redis en produccion en el
proximo despliegue, sin que nadie lo hubiera probado alli. Se deja en `<6.0` con
el motivo escrito en el propio `requirements.txt`, para que no se "arregle"
hacia arriba por inercia.

**La deriva de Python es la mas seria de las dos**, y no estaba anotada en
ningun sitio. El proyecto declara `requires-python = ">=3.12"` y `black`/`ruff`
apuntan a `py312`, que es lo que corre en produccion; desarrollar y validar
sobre 3.14 significa que **la suite verde local no prueba el interprete que
opera**. Entre 3.12 y 3.14 hay cambios de comportamiento en `asyncio` y en
`typing` que este proyecto usa intensivamente.

Nota de contexto: la deuda del Bloque 1 (los 3 errores de mypy causados por
`types-redis`) se diagnostico asumiendo redis 8.0 — cierto en el entorno de
desarrollo, no en produccion. La conclusion (desinstalar los stubs obsoletos)
sigue siendo correcta, porque el override de `app.cache.redis_backend` en
`pyproject.toml` es justamente el que cubre 5.x.

**Pendiente, no resuelto aqui:** alinear el entorno de desarrollo a Python 3.12
y redis 5.3.1, o decidir conscientemente subir produccion. No lo hago por mi
cuenta: cambiar el interprete o los paquetes de la maquina del operador excede
lo que pide el pendiente.

## ADR-098 · La volatilidad se clasifica por simbolo, con umbrales del timeframe real

**Contexto.** `MarketContext.volatility` llegaba `normal` en el **100 %** de las
1209 operaciones del journal de produccion. No era un feature roto: era una
calibracion de otra escala temporal.

Medido: el ATR% en 1m tiene mediana 0.038-0.068 % y **maximo observado 0.261 %**.
El umbral `atr_pct_high` valia **0.80 %** — inalcanzable por construccion. El
otro lado lo cerraba el `.env` de `qevps`, que bajaba `atr_pct_low` de 0.05 a
0.02 (por debajo del p5 real). La banda `[0.02, 0.80]` capturaba absolutamente
todo.

**Por que importa mas de lo que parece.** Una variable constante no es un dato
neutro: es una feature muerta que consume su sitio. El `ConfidenceEngine` la
pondera, los filtros la consultan, y el ML la recibe como columna de entrada con
varianza cero — donde no aporta nada pero **si diluye** el peso relativo de las
que si informan.

**Decision.** Umbrales derivados de la distribucion real en 1m (p≈25 y p≈85), y
**por simbolo**, con el global como fallback — mismo patron de resolucion en
escalones que ADR-083 usa para el holding.

**Por que por simbolo.** La escala de ATR% no es comparable entre activos: la
mediana en 1m es 0.038 % en oro y 0.068 % en ETH. Un unico par de umbrales
marcaria al oro como LOW casi siempre y a ETH casi nunca — cambiando una
constante inutil por otra igual de inutil, solo que menos evidente.

**Consecuencias.** Los tests fijan el contrato contra la distribucion real
medida, no contra numeros elegidos: si alguien sube el umbral fuera del rango
observado, el test que exige que HIGH sea alcanzable falla. Se anadio ademas
`quant.context.atr_pct_high` a la whitelist del Config Center — **faltaba**, asi
que el umbral bajo se podia ajustar en caliente y el alto, que era el mal
calibrado, no.

## Calibracion de stops y objetivos: por que recalibrar NO arregla la expectativa

**El hallazgo mecanico.** `sizing.atr_stop_multiplier = 1.5` **no se aplica
nunca en produccion**. La distancia del stop es
`max(ATR x 1.5, piso_porcentual, piso_de_spread)` y uno de los dos pisos gana
siempre:

| Simbolo | ATR | ATR x1.5 | piso 0.15 % | piso spread x8 | stop real | manda |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDm | 5.4 bps | 8.2 | **15.0** | 12.5 | 15.0 | `min_stop_pct` |
| ETHUSDm | 6.8 bps | 10.2 | 15.0 | **42.2** | 42.0 | `spread x8` |
| USTECm | 5.9 bps | 8.9 | **15.0** | 10.3 | 15.0 | `min_stop_pct` |
| XAUUSDm | 3.8 bps | 5.7 | **15.0** | 4.7 | 15.0 | `min_stop_pct` |

El stop deja de ser adaptativo a la volatilidad, y el objetivo
(`stop x reward_risk`) hereda el problema: acaba a 4-8x ATR. El precio en 1m
recorre tipicamente **1-3x ATR** antes de que la operacion termine (mediana de
1.22x ATR en las salidas por regimen), asi que el objetivo casi nunca se alcanza
— 52 take-profits en 1209 operaciones.

**ETH es un caso aparte.** Su spread (5.27 bps) es el **78 %** de su ATR
(6.8 bps). El piso de spread lo empuja a un stop de 6.2x ATR y un objetivo de
9.3x ATR. A esa relacion coste/movimiento, el scalping de ETH no es viable
independientemente de la calibracion.

**La validacion, que es lo que decide.** Se barrieron `min_stop_pct` (0.15 →
0.03), `atr_stop_multiplier` (1.0-2.0) y `reward_risk` (1.5 / 1.2) corriendo el
**QuantCore real** sobre velas 1m reales de Binance (`scripts/calibrate_stops.py`):

- **Ninguna combinacion da expectativa positiva.** La mejor (R:R 1.2) pasa de
  −0.083R a −0.049R en ETH: mejora, pero no cruza cero.
- **En ETH, bajar `min_stop_pct` de 0.15 a 0.03 no cambia literalmente nada**
  (120 operaciones, 48.3 % WR, PF 0.72 en las cuatro filas). Confirmacion
  empirica de que su stop lo fija el piso de spread.
- **Lo decisivo: tambien pierde a spread CERO** (PF 0.11-0.56 en todas las
  combinaciones, ambos simbolos). **El problema no es el coste ni la
  calibracion de los niveles: son las senales de entrada.**

**Consecuencia.** No se cambia ningun parametro de sizing: seria mover numeros
sin evidencia de mejora. El trabajo pendiente esta en la calidad de las senales,
no en donde se ponen los niveles. Coherente con lo medido en ADR-097 (las
operaciones que resuelven su propia tesis dan −0.252R) — y con que el Bloque 1
no mejorase la expectativa: alargar el holding da mas tiempo para llegar a un
objetivo que esta fuera de alcance, y mas tiempo para tocar el stop.

**Deuda anotada:** el `atr_stop_multiplier` es hoy codigo muerto. O se le da
efecto (bajando los pisos, con la contrapartida de que el ruido del spread
barreria el stop) o se elimina para que la configuracion no prometa una
adaptatividad que no existe. No se resuelve aqui porque no cambia el resultado.

## Hallazgo: la expectativa por estrategia no se replica entre simbolos (2026-08-04)

**Pregunta.** El edge se habia medido siempre sobre el portfolio agregado ("el
motor da -0.078R"), lo que no distingue *todas pierden un poco* de *unas pocas
pierden mucho y tapan a las que ganan*. La primera situacion obliga a rehacer el
enfoque; la segunda, solo a podar el catalogo.

**Metodo.** `scripts/strategy_edge.py` corre **cada estrategia sola** (las otras
19 desactivadas) a traves del QuantCore real —mismos filtros, sizing y Execution
Engine que en vivo— sobre 10.000 velas 1m reales de Binance (~7 dias), para BTC
y ETH por separado. Con una sola estrategia el consenso es unanime por
construccion, asi que lo que se mide es la senal de esa estrategia.

**Resultado.** De 14 estrategias con >=10 operaciones en ambos simbolos:

| | BTC | ETH |
| --- | --- | --- |
| Con expectativa positiva | 2 | 3 |
| **Positivas en AMBOS** | **0** | |
| Cambian de signo entre simbolos | 5 | |
| Negativas en ambos | 9 | |

**Correlacion de la expectativa entre BTC y ETH: r = +0.084.** Es decir,
practicamente cero: saber como le fue a una estrategia en BTC no dice **nada**
sobre como le ira en ETH.

Los casos extremos lo ilustran mejor que el promedio: `mss` da **+0.106R en ETH
y -0.812R en BTC**; `vwap_breakout` es la mejor de BTC (+0.211R) y negativa en
ETH (-0.053R). No es que unas estrategias funcionen y otras no: **el ranking en
si es ruido**.

**Consecuencias, y son las que importan:**

1. **Podar el catalogo no funcionaria.** Seleccionar las "ganadoras" medidas en
   un simbolo daria las perdedoras del otro. No hay nada estable que conservar.
2. **La ponderacion dinamica del Meta Strategy Manager esta ajustando ruido.**
   Reponderar por rendimiento reciente asume que ese rendimiento persiste; con
   r=0.084 entre dos activos altamente correlacionados en el mismo timeframe,
   esa premisa no se sostiene. El MSM no esta mejorando el consenso: le esta
   metiendo varianza.
3. **Entrenar el ML sobre esto seria ajustar ruido con mas parametros.** Es la
   respuesta a "si el bot aprende de las estrategias perdedoras, mejorara": no,
   porque no hay una senal estable que aprender.
4. **Tercera confirmacion independiente del Bloque 9:** `cvd`,
   `delta_confirmation` y `orderbook_imbalance` produjeron **0 operaciones** en
   ambos simbolos, igual que en produccion. Sin libro ni operaciones no disparan.

**Matiz que no invalida lo anterior pero conviene registrar:** varias filas
tienen expectativa en R positiva y **retorno en dinero negativo** (p. ej.
`vwap_breakout` +0.211R con -0.01 %). La R positiva no se convierte en dinero,
lo que apunta a que el sizing y los costes se comen el margen — coherente con
que el problema tampoco sea la calibracion de niveles.

**Limites de esta medicion.** 7 dias, 2 simbolos, un unico periodo de mercado, y
sobre spot de Binance (no el CFD que opera). Las muestras por estrategia son de
10-72 operaciones. Nada de esto prueba que las estrategias sean irreparables;
prueba que **con los datos disponibles no hay evidencia de edge en ninguna, ni
de un ranking estable entre ellas**. La forma correcta de refutarlo es un
walk-forward con mas historia y mas simbolos, que el laboratorio ya soporta.


## El motor es monotimeframe, y el regimen casi no entra en la decision (2026-08-04)

**Verificado en codigo.** No hay analisis multi-timeframe en ninguna parte del
camino de decision:

| Componente | Timeframe | Ventana |
| --- | --- | --- |
| Las 20 estrategias | 1m | - |
| `RegimeDetector` | 1m, lookback 50 | **50 minutos** |
| `MarketContextEngine` | 1m | - |
| ATR (periodo 14) | 1m | 14 minutos |
| Feature Store (default) | 1m | - |

Los unicos sitios donde aparecen 5m/15m/1h/4h son normalizadores y proveedores
-la fontaneria que sabria parsearlos-. **Nada en el camino de decision pide otra
cosa que 1m**, y el agregador construye velas de 5m que nadie consume.

Es una hipotesis plausible para el resultado de `strategy_edge.py`: media
biblioteca son conceptos de *estructura de mercado* (`bos`, `choch`, `mss`,
`order_block`, `fair_value_gap`) y la estructura leida en velas de 1 minuto se
rompe cada pocos minutos. Detectar estructura ahi es detectar ruido - lo que
explicaria que el ranking no se replique entre simbolos (r = +0.084).

### El experimento, y por que NO resuelve la hipotesis

Se construyo la agregacion a marcos superiores sin lookahead
(`app/backtesting/htf.py`, 9 tests) y se barrio el timeframe del detector de
regimen de 1m a 1h manteniendo la entrada en 1m
(`scripts/multi_timeframe.py`). **Los seis escenarios dan resultados
practicamente identicos** (9 operaciones, mismo WR, mismo PF; la expectativa
varia en la tercera decimal).

Eso no refuta la hipotesis: revela que **el regimen apenas alimenta la
decision**. Auditado:

- La cadena de filtros **no lo consulta** en absoluto.
- El consenso en uso es `weighted_average`, que **no aplica**
  `regime_multipliers` (solo lo haria `regime_weighting`).
- De las 20 estrategias, solo `mean_reversion` lo lee directamente, mas el motor
  de confirmaciones y un gate en `strategies/shared/api.py`.

Cambiar el marco de una senal que casi nadie escucha no puede cambiar el
resultado. **La hipotesis multi-timeframe queda sin probar, no descartada.**

### Hallazgo colateral, y es el mas serio: el backtest no reproduce las salidas

`app/backtesting/simulator/execution_factory.py` construye el `ExecutionEngine`
con **`context=None`**. En esa rama, `_market_view` devuelve `regime="unknown"`
y `volatility="normal"` de forma fija. Consecuencia:

**La salida por cambio de regimen -el 72 % de los cierres en produccion- no
existe en el backtest.**

Implicaciones en las dos direcciones, porque las tiene:

- **Refuerza** la conclusion de que no hay edge en las senales. El backtest mide
  las estrategias con salidas limpias de SL/TP, sin interferencia del regimen -
  es la prueba mas favorable posible para la senal - y aun asi pierden a spread
  cero.
- **Debilita** cualquier lectura de la *mezcla de salidas* o de la duracion
  media en backtest: ahi el backtest y produccion son sistemas distintos.

Es deuda de fidelidad del laboratorio, no un bug de esta sesion, pero conviene
tenerla escrita antes de seguir usando el backtest para decidir.

**Siguiente paso para probar de verdad la hipotesis multi-timeframe:** no basta
mover el timeframe del detector; hay que **dar efecto al regimen en la
decision** - un filtro de sesgo que impida abrir contra la estructura del marco
superior. Eso es funcionalidad nueva, no un barrido de configuracion.

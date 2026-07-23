# Quant Engine V2

Motor cuantitativo modular para **scalping de criptomonedas y XAUUSD (oro)**.
Opera primero en **Paper Trading** y pasa a **Live Trading** únicamente cuando
se cumplan criterios estadísticos definidos.

> **Estado actual: Fase 7 — Machine Learning, IA y aprendizaje continuo
> (v0.7.0).** Sobre la ejecución (Fase 5) y el laboratorio de backtesting
> (Fase 6) vive ahora la capa de ML (`app/ml/`, fachada `MLEngine`): modelos
> intercambiables en Python puro, Feature Store profesional versionado, Model
> Registry con rollback, entrenamiento honesto (holdout + walk-forward) con
> puerta de validación que **nunca activa un modelo inferior**, predicción
> explicable, detección de deriva, AutoML y un **Meta Strategy Manager** que
> gobierna el peso de las estrategias por configuración. Se entrena con el
> historial del propio motor (el Trade Journal), notifica por Discord y expone
> `/api/ml/*`. **Regla absoluta: el ML asesora, no decide** — ninguna
> recomendación abre operaciones ni habilita live; el Decision Engine y el Risk
> Manager tienen la última palabra y el sistema sigue **solo en paper trading**.
>
> _Fase 5:_ Execution Engine + Paper Trading de alta fidelidad (spread,
> slippage, latencia, comisiones, rechazos, gaps; Risk Manager con kill switch y
> circuit breaker). _Fase 6:_ laboratorio de backtesting, optimización y
> validación estadística (Strategy Qualification Pipeline). _Fase 4:_ biblioteca
> de 20 estrategias como plugins que analizan, puntúan, explican y proponen
> Entry/SL/TP — nunca operan por sí mismas.

## Principios

- Python 3.12 · Clean Architecture · SOLID · DRY · Event-Driven
- Tipado completo (MyPy strict) · Docstrings Google · Ruff + Black · Pytest
- Configuración 100 % por variables de entorno (`.env` + `config/<ambiente>.env`)
- **Notificaciones exclusivamente por Discord Webhook** (regla global)
- Diseñado para operar 24/7 sobre asyncio, sin bloqueos

## Arquitectura (Fase 1)

```
                 ┌────────────────────────────────────────────┐
                 │                QuantEngine                 │
                 │   (orquestador de ciclo de vida, DI)       │
                 └───────┬────────────────────────────────────┘
                         │ start/stop ordenado
   ┌───────────┬─────────┼──────────┬───────────┬───────────┐
   ▼           ▼         ▼          ▼           ▼           ▼
 EventBus   Cache     Notific.   Scheduler   Watchdog   HealthMonitor
 (asyncio) (Redis→mem) (Discord)  (jobs)     (reinicios) (CPU/RAM/lag)
   ▲                                                        │
   └────────────── eventos ────────────────────────────────┘
                         ▲
                         │ REST + WebSocket
                    FastAPI (ApiService) ──► Dashboard Next.js (fase futura)
```

- Los módulos **solo** se comunican por eventos (Event Bus) o interfaces
  (`app/core/interfaces`). Nada importa implementaciones de otro módulo.
- El grafo de dependencias se arma únicamente en `app/engine/bootstrap.py`
  (composition root) mediante el contenedor DI.

## Estructura

```
app/
  core/          eventos, excepciones, interfaces, DI, ciclo de vida
  engine/        QUANT CORE (Fase 3): orquestador + composition root,
                 strategy/signal/decision engines, consenso, confianza,
                 contexto/régimen, filtros, Feature Store, historial,
                 evaluación continua (Fase 4)
  config/        settings tipados por ambiente (pydantic-settings)
  logging/       rotación, archivo por módulo, JSON opcional, buffer de errores
  notifications/ servicio + canal Discord (webhook)
  cache/         Redis primario con degradación automática a memoria
  database/      SQLAlchemy async + Alembic (sin tablas aún)
  scheduler/     tareas periódicas asyncio
  monitoring/    HealthMonitor + Watchdog
  dashboard/api/ FastAPI: REST + WebSocket de eventos (+ /api/market/*)
  documentation/ bitácora (Markdown hoy, Notion preparado)
  market/        DATA ENGINE (Fase 2):
    providers/     Binance/Bybit/OKX (activos) + Bitget/OANDA/MT5/IBKR (preparados)
    normalizer/    formato de cada exchange → modelo interno único
    stream/        WSConnection (reconexión/backoff/heartbeat) + métricas
    collector/     pipeline: validar → estado → velas → libro → cache → storage
    validator/     calidad de datos (duplicados, gaps, corruptos)
    aggregator/    trades → velas de cualquier timeframe
    feed/          orquestación de proveedores + subscribe("SYMBOL")
    services/      MarketDataService (API interna), estado vivo, order books
    storage/       market_ticks/market_candles + writer batched con spill
    cache/         último valor de todo en Redis (mkt:*)
    scheduler/     jobs: flush de velas, conexiones, drift de reloj, métricas
    models/ events/ interfaces/
  strategies/    BIBLIOTECA DE ESTRATEGIAS (Fase 4): 20 plugins en
    base/          QuantStrategy: pipeline score/confianza/explicación
    trend/ momentum/ orderflow/ smc/ volume/ volatility/
    mean_reversion/ breakout/    las estrategias (una por archivo)
    confirmation/  motor de confirmaciones bajo demanda
    filters/ shared/ utils/      pre-chequeos, APIs internas, helpers
  analytics/
    indicators/  funciones puras: SMC, order flow, VWAP, volume profile,
                 ATR, momentum, market structure, liquidez
  execution/ brokers/ risk/ portfolio/ orderflow/
  ml/ optimizer/ backtesting/ paper/ live/   ← fases futuras
tests/           unit + integration (pytest, asyncio)
docs/            arquitectura, decisiones, bitácora, convenciones
docker/          Dockerfiles + Prometheus/Grafana preparados
config/          overlays por ambiente (development/testing/paper/production)
```

## Puesta en marcha

```bash
cp .env.example .env        # completar secretos (webhook Discord, DB)
docker compose up -d        # backend + PostgreSQL + Redis
# perfiles opcionales:
docker compose --profile monitoring up -d   # Prometheus + Grafana
docker compose --profile timescale up -d    # TimescaleDB
```

API: `http://localhost:8000/api/health` · `http://localhost:8000/api/system/status`
· `http://localhost:8000/api/market/status` · `/api/market/price/BTCUSDT`
· `/api/market/orderbook/BTCUSDT` · `/api/market/candles/BTCUSDT?tf=1m`
· WebSocket `ws://localhost:8000/ws/events` · OpenAPI `http://localhost:8000/docs`

### Desarrollo local (sin Docker)

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements-dev.txt
python -m app                                     # arranca el motor
```

## Calidad

```bash
ruff check .          # lint
black --check .       # formato
mypy                  # tipos (strict)
pytest --cov          # tests + cobertura
```

Todo lo anterior corre en CI (GitHub Actions) en cada push/PR a `main`.

## Ambientes

| Ambiente     | Archivo                  | Uso                                    |
|--------------|--------------------------|----------------------------------------|
| development  | `config/development.env` | desarrollo local (DEBUG)               |
| testing      | `config/testing.env`     | pytest/CI — **Discord forzado OFF**    |
| paper        | `config/paper.env`       | paper trading 24/7 (logs JSON)         |
| production   | `config/production.env`  | live trading (fase final)              |

Selección: variable `QE_ENVIRONMENT`. Precedencia de configuración:
entorno del proceso → `config/<ambiente>.env` → `.env`.

## Documentos

- [ROADMAP.md](ROADMAP.md) — fases del proyecto
- [CHANGELOG.md](CHANGELOG.md) — historial de cambios
- [docs/architecture.md](docs/architecture.md) — decisiones técnicas (ADRs)
- [docs/conventions.md](docs/conventions.md) — convenciones de commits y código
- [docs/bitacora.md](docs/bitacora.md) — bitácora del proyecto (DocumentationService)

# Quant Engine V2 — Documentación del proyecto y operación

> Documento vivo. Última actualización: 2026-07-19 (v0.10.0).
> Regla de oro absoluta: **el sistema opera SOLO en paper trading. Live sigue
> deshabilitado** (`allow_live=False`, `resolved_mode()→paper`). Ninguna pieza lo
> abre; el paso a live es una decisión humana bajo el Live Gate (Fase 9).

Índice:
1. Qué es
2. Stack y reglas del proyecto
3. Arquitectura por fases (1–10)
4. Estructura del repo
5. Despliegue en el VPS (runbook)
6. Operación diaria
7. Gotchas (cosas que muerden)
8. Estado actual y pendientes

---

## 1. Qué es

Quant Engine V2 es un **motor cuantitativo modular** para scalping de
criptomonedas y XAUUSD. Es un rebuild desde cero (repo propio, separado del
QuantEngine original de MT5). Descubre oportunidades, decide con consenso de
estrategias, ejecuta en **paper trading** de alta fidelidad, aprende con ML e
investiga nuevas estrategias de forma autónoma — todo observable desde un
dashboard y notificado por Discord.

- **Fases 1–10 completas**, versión **v0.10.0**.
- **Python puro**: cero numpy/pandas/scipy/pyarrow (instalación trivial, sin
  compilar; el problema del CPU Bobcat sin SSE4.2 no aplica).
- **644 tests** en verde; `ruff` + `black` + `mypy --strict` limpios en `app`.
- **Notificaciones exclusivamente por Discord** (webhook en `.env`).

## 2. Stack y reglas del proyecto

- Python 3.12, Clean Architecture + SOLID, DI por contenedor explícito
  (composition root **solo** en `app/engine/bootstrap.py`).
- Arquitectura orientada a eventos (EventBus asyncio propio).
- Config con pydantic-settings: prefijo `QE_`, anidado con `__`, 4 ambientes
  (`development` / `testing` / `paper` / `production`). `testing` fuerza Discord
  OFF.
- FastAPI para la API; PostgreSQL (SQLAlchemy async + Alembic) y Redis (cache
  degradable a memoria).
- Calidad: `ruff`, `black`, `mypy --strict`, `pytest`. **Nunca commits
  automáticos** — cada commit es una decisión humana.
- Docs: ADRs en `docs/architecture.md`, bitácora en `docs/bitacora.md`.

## 3. Arquitectura por fases (1–10)

| Fase | Nombre | Qué aporta |
|------|--------|-----------|
| 1 | Infraestructura | EventBus, config, logging, DI, cache degradable, DB, scheduler, health, watchdog, notificaciones Discord, API, Docker, CI |
| 2 | Data Engine (`app/market`) | Proveedores Binance/Bybit/OKX (WS+REST), normalización, agregador de velas, order book, cache Redis, persistencia batched |
| 3 | Quant Core (`app/engine`) | Strategy Engine (plugins), Signal Engine, **Decision Engine** (consenso, confianza, contexto, régimen, filtros con veto — siempre explicable) |
| 4 | Biblioteca de estrategias (`app/strategies`) | 20 estrategias (VWAP, SMC, order flow, volume profile, ORB, momentum, ATR, mean reversion…); indicadores puros en `app/analytics` |
| 5 | Execution Engine (`app/execution`) | **Paper trading** de alta fidelidad (spread, slippage, latencia, comisiones, rechazos), Risk Manager (kill switch, circuit breaker), portfolio/position/order managers, Trade Journal |
| 6 | Backtesting (`app/backtesting`) | Motor de backtest, walk-forward, Monte Carlo, benchmark, optimizadores, Strategy Qualification Pipeline |
| 7 | Machine Learning (`app/ml`) | Modelos en Python puro, Feature Store versionado, Model Registry con rollback, drift, AutoML, Meta Strategy Manager. **El ML asesora, no decide** |
| 8 | Dashboard (`dashboard/`) | Frontend Next.js + React + TS (14 pantallas) sobre la API REST/WS; capa de comandos del backend con guard anti-live y audit log |
| 9 | Producción / DevOps (`app/production`, `app/security`) | Live Gate fail-closed, Safe Mode, Kill Switch, Recovery, Notion real, reportes/backups automáticos, Continuous Improvement, seguridad, failover |
| 10 | Quant Research Lab (`app/research`) | Laboratorio autoevolutivo: genera, valida y promueve estrategias sobre copias. Generador por reglas, Feature/Factor Lab, optimización multiobjetivo + bayesiana (TPE), Candidate Pipeline, **Shadow Mode**, Promotion Manager fail-closed. **No opera; la promoción exige aprobación humana** |

Detalle técnico y decisiones (ADR-001…082): `docs/architecture.md`.
Docs por área: `docs/research.md`, `docs/ml.md`, `docs/backtesting.md`,
`docs/execution.md`, `docs/strategies.md`, `docs/dashboard.md`.

## 4. Estructura del repo

```
app/
  engine/        # composition root (bootstrap.py) + Quant Core
  market/        # Data Engine
  strategies/    # 20 estrategias + analytics
  execution/     # paper trading + riesgo
  backtesting/   # laboratorio Fase 6
  ml/            # machine learning
  research/      # Quant Research Lab (Fase 10)
  production/    # capa de producción / DevOps
  dashboard/api/ # API FastAPI (rutas /api/*, /ws/events, /docs)
  config/        # settings.py (pydantic) + overlays por ambiente
  core/          # EventBus, DI container, excepciones, lifecycle
dashboard/       # frontend Next.js
docs/            # arquitectura, ADRs, bitácora, docs por área
tests/           # unit + integration (644 tests)
config/*.env     # overlays no sensibles por ambiente
.env             # secretos locales (NO se commitea)
```

## 5. Despliegue en el VPS (runbook)

**Servidor:** Windows, `192.168.100.80`, usuario `mt5` (admin).
**Acceso:** `ssh qevps` (alias configurado, auth por llave). Shell remoto:
**PowerShell**.

> Nota: este mismo VPS también corre el **QuantEngine original** (MT5) bajo
> `C:\Users\MT5\Desktop\QuantEngine` + Python 3.11 — **no tocar esos procesos**.

### Lo instalado (todo bajo `C:\Users\MT5\`)

| Componente | Ubicación / servicio | Detalle |
|---|---|---|
| Python 3.12.10 | `AppData\Local\Python\pythoncore-3.12-64\` (via PyManager, `py -3.12`) | El app requiere 3.12+ (genéricos PEP 695) |
| Node 22.12.0 | `node\` (zip portable) | Para el dashboard Next.js |
| PostgreSQL 16.9 | binarios `pgsql\`, datos `pgdata\`, **servicio `postgresql-16`** (Automatic, `:5432`) | DB `quantengine`, superusuario `postgres` (password solo en `.env`) |
| Redis 5.0.14 | `redis\`, **servicio `Redis`** (Automatic, `:6379`) | Cache (la app degrada a memoria si falta) |
| Repo | `QuantEngineV2\` | venv en `QuantEngineV2\.venv` (Python 3.12) |

### Configuración (`C:\Users\MT5\QuantEngineV2\.env`)

- `QE_ENVIRONMENT=paper`
- DB: `postgres@localhost:5432/quantengine`
- Cache: `localhost:6379`
- Discord ON (webhook real)
- **Activados:** `QE_EXECUTION__ENABLED`, `QE_ML__ENABLED`, `QE_RESEARCH__ENABLED`
- Capa de producción (Fase 9): **OFF** (se puede activar con `QE_PRODUCTION__ENABLED=true`)
- `QE_DASHBOARD__CORS_ORIGINS=["http://localhost:3000","http://192.168.100.80:3000"]`

Base de datos migrada a Alembic **head 0002** (`market_ticks`, `market_candles`,
`strategy_signals`, `engine_decisions`).

### URLs

- **Dashboard visual:** http://192.168.100.80:3000
- **API + Swagger navegable:** http://192.168.100.80:8000/docs
- **Health:** http://192.168.100.80:8000/api/health

Firewall abierto para `8000` (API) y `3000` (dashboard).

### Arrancar (desacoplado, sobrevive al cierre de SSH)

`Start-Process` NO sirve (Bitvise mata el hijo al cerrar la sesión). Usar
**`Win32_Process.Create`**:

```powershell
ssh qevps
$d = "$env:USERPROFILE\QuantEngineV2"

# Engine (backend :8000)
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine  = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$d+'\start.ps1"'
  CurrentDirectory = $d }

# Dashboard (Next.js :3000)
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine  = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$d+'\dashboard\start_dashboard.ps1"'
  CurrentDirectory = "$d\dashboard" }
```

`start.ps1` fija `QE_ENVIRONMENT=paper` y corre `python -m app` (log en
`logs\engine.log`). `dashboard\start_dashboard.ps1` corre el server standalone de
Next (log en `dashboard\dashboard.log`).

### Parar

```powershell
# Engine:
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess -Force
# Dashboard:
Stop-Process -Id (Get-NetTCPConnection -LocalPort 3000 -State Listen).OwningProcess -Force
```

## 6. Operación diaria

```powershell
ssh qevps
$d = "$env:USERPROFILE\QuantEngineV2"

# ¿Está vivo?
(Invoke-WebRequest http://localhost:8000/api/health -UseBasicParsing).StatusCode

# Logs
Get-Content $d\logs\engine.err.log -Tail 40
Get-Content $d\dashboard\dashboard.log -Tail 20

# ¿Cuántos datos ha ingerido? (Postgres)
$env:PGPASSWORD = (Select-String -Path $d\.env -Pattern '^QE_DATABASE__PASSWORD=').Line.Split('=',2)[1]
& $env:USERPROFILE\pgsql\bin\psql.exe -U postgres -h localhost -d quantengine -tAc `
  'select (select count(*) from market_ticks), (select count(*) from strategy_signals)'
```

- **Dashboard:** http://192.168.100.80:3000 (pantallas de mercado, estrategias,
  ML, ejecución, producción, backtesting, etc.).
- **API/Swagger:** http://192.168.100.80:8000/docs (ejecutar cualquier endpoint
  desde el navegador).
- **Discord:** el bot notifica arranque, señales, ejecución, ML, research y
  errores al webhook configurado.

## 7. Gotchas (cosas que muerden)

1. **No arrancar el engine dos veces.** Si el puerto 8000 ya está ocupado,
   uvicorn lanza `SystemExit(3)` que **tumba todo el engine**. Verificar antes de
   arrancar que no haya una instancia corriendo.
2. **`QE_ENVIRONMENT` debe ser variable de PROCESO**, no solo del `.env`.
   `detect_environment()` la lee del entorno del proceso para elegir el overlay.
   `start.ps1` ya la exporta.
3. **Detach real = `Win32_Process.Create`.** `Start-Process` no sobrevive al
   cierre de la sesión SSH en este Bitvise.
4. **Dashboard Next.js con `output: standalone`:** `next start` NO funciona; hay
   que correr `node .next/standalone/server.js` y copiar `.next/static` y
   `public` dentro de `.next/standalone/`. El `.env.local` con
   `NEXT_PUBLIC_API_BASE=http://192.168.100.80:8000` debe existir **antes** del
   `npm run build` (se hornea en el bundle del navegador).
5. **CORS:** para acceder al dashboard desde la red, el backend debe permitir el
   origen `http://192.168.100.80:3000` (`QE_DASHBOARD__CORS_ORIGINS`) y reiniciar
   una vez.
6. **Event stream del dashboard:** `/ws/events` filtra eventos de alta frecuencia
   (`NewTick`, `CandleClosed`, order book, detecciones por-barra…) para no
   inundar; solo muestra hitos accionables. El tipo va en la clave `event` del
   JSON.

## 8. Estado actual y pendientes

**Corriendo ahora en el VPS:** engine (paper: Market + Quant + Execution + ML +
Research) en `:8000` y dashboard en `:3000`, ambos desacoplados.

**Pendientes / mejoras posibles:**
- **Resiliencia 24/7:** hoy los procesos sobreviven al cierre de SSH pero **NO a
  un reinicio del VPS** y **no se auto-reinician si se caen**. Para blindarlo:
  tarea programada al boot + auto-restart, o un servicio (NSSM), para engine y
  dashboard.
- **Capa de producción (Fase 9)** apagada; activarla suma live-gating, reportes
  y backups automáticos.
- **Revisión ML del Candidate Pipeline** (Fase 10) desactivada por defecto:
  necesita historial de operaciones para ser útil.
- **Nada commiteado** aún (regla del proyecto: commits manuales). El fix del
  event stream ya está desplegado en el VPS pero pendiente de commitear.
- **Espejo en Notion** de las últimas fases (regla del usuario) pendiente.

---
Contacto operativo: el motor es paper-only; cualquier paso hacia live pasa por el
Live Gate de la Fase 9 y es una decisión humana explícita.

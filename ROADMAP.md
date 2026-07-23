# Roadmap — Quant Engine V2

## ✅ Fase 1 — Infraestructura
Base arquitectónica: Event Bus, configuración por ambientes, logging,
excepciones, DI, cache degradable, DB preparada, scheduler, health monitor,
watchdog, notificaciones Discord, API FastAPI, Docker, CI, tests.
**Sin trading, sin ML, sin brokers.**

## ✅ Fase 2 — Data Engine
- Proveedores Binance/Bybit/OKX funcionales; Bitget/OANDA/MT5/IBKR preparados.
- WebSocket manager (reconexión, heartbeat, resuscripción, métricas) + REST
  de reconstrucción; normalización por exchange al modelo interno único.
- Pipeline: validación de calidad → estado vivo → agregador de velas →
  order book incremental → cache Redis → persistencia batched con spill.
- `MarketDataService` (API interna agnóstica del broker) + eventos +
  endpoints `/api/market/*` + primeras tablas (migración Alembic 0001).
- Pendiente de fase: implementar MT5/OANDA para XAUUSD real; backfill
  histórico; reconciliación de spills.

## ✅ Fase 3 — Quant Core y Strategy Engine
- Framework de estrategias (`BaseStrategy` + plugins con carga dinámica) que
  consume exclusivamente el Data Engine; las estrategias nunca operan.
- Ejecución concurrente con cadencia propia (tick/segundo/vela/intervalo),
  aislamiento de errores y métricas de tiempo por estrategia.
- Decision Engine único: consenso configurable (5 algoritmos), confianza
  separada del score, contexto de mercado, detección de régimen, filtros con
  veto y decisiones SIEMPRE explicables.
- Feature Store central (cálculo único), historial completo de señales y
  decisiones persistido (Alembic 0002), endpoints `/api/engine/*`.
- Pendiente de fase: módulo de riesgo con veto (`RiskTriggered`);
  backtesting histórico.

## ✅ Fase 4 — Biblioteca de estrategias (actual)
- 20 estrategias profesionales como plugins (`app/strategies/`): VWAP
  (reversion/breakout/anchored), SMC (sweep, OB, FVG, BOS, CHOCH, MSS),
  order flow (delta, CVD, imbalance), volume profile, ORB, momentum,
  ATR expansion, compresión de volatilidad, pullback, mean reversion,
  range breakout. Nunca ejecutan órdenes.
- Indicadores como funciones puras (`app/analytics/indicators/`): SMC,
  order flow, VWAP+bandas, volume profile, ATR avanzado, momentum,
  market structure, liquidez — cacheados vía Feature Store.
- Base `QuantStrategy` (pre-chequeos → evaluate → confirmaciones → score
  5 componentes → confianza → señal explicable) + motor de confirmaciones.
- Framework de Evaluación Continua: operaciones virtuales por señal →
  win rate, PF, expectativa (R), drawdown, falsas señales, tiempo medio;
  aún NO se usa para operar.
- APIs internas estables (`detect_*`/`calculate_*`), eventos de detección,
  endpoints de detalle por estrategia.
- Pendiente de fase: parámetros editables desde el dashboard (la interfaz
  llega en su fase); optimización automática (fase ML).

## Fase 5 — Execution Engine y Paper Trading ✅ (v0.5.0)
- Capa de ejecución completa (`app/execution/`) desacoplada: Decision →
  Risk Manager → Execution Engine → Paper Engine → Position/Portfolio
  Manager → Trade Journal → eventos → Discord. **Solo paper trading.**
- Paper Engine de alta fidelidad: spread, slippage (volatilidad/liquidez/
  tamaño/sesión/tipo), latencia con deriva de precio, comisiones, rechazos,
  parciales y gaps. Nunca asume ejecución perfecta.
- Risk Manager profesional: límites por operación/día/semana/mes, pérdidas
  consecutivas, exposición total/símbolo/correlación, filtros de spread y
  liquidez, kill switch (drawdown) y circuit breaker (pérdida rápida).
- Position sizing (fijo/%/ATR/riesgo fijo/dinámico/Kelly parcial), gestión
  dinámica (break-even, trailing por ATR, salida por tiempo/régimen).
- Portfolio Manager (balance/equity/drawdown/exposición), Order Manager
  (ciclo de vida + OCO preparado), Trade Journal exhaustivo y Performance
  Engine (win rate, PF, expectativa, Sharpe/Sortino/Calmar, Ulcer...).
- Notificaciones Discord por evento + reportes periódicos; endpoints del
  dashboard `/api/execution/*`.
- Pendiente de fase: **criterios estadísticos de paso a live** (definidos y
  automatizados) antes de habilitar cualquier operación con dinero real.

## Fase 7 — Machine Learning, IA y aprendizaje continuo ✅ (v0.7.0)
- Capa `app/ml/` completa (fachada `MLEngine`): modelos intercambiables en
  **Python puro** (logística/árbol/random forest/extra trees) más backends
  pesados y ensembles (voting/stacking) preparados; **Feature Store profesional**
  versionado; **Model Registry** con activación reversible (rollback); entrenamiento
  honesto (holdout + walk-forward) con **puerta de validación que nunca activa un
  modelo inferior**; predicción explicable; **detección de deriva**; AutoML; AI
  Advisor; Strategy Intelligence.
- **Meta Strategy Manager**: gobierna activación, prioridad y peso de las
  estrategias por configuración —nunca el código, nunca opera— con auditoría.
- Notificador Discord del ML y endpoints `/api/ml/*`; cableado en el composition
  root (se entrena con el Trade Journal) + jobs del scheduler (nocturno/deriva/
  meta). **Regla absoluta: el ML asesora, no decide; sigue en paper trading.**
- Pendiente de fase: entrenar la infra preparada (online/incremental, GPU,
  paralelo, refuerzo por bandit); habilitar los backends pesados; conectar el
  `QuantCore` real como fuente de decisiones para el aprendizaje.

## Fase 8 — Dashboard profesional y centro de control ✅ (v0.8.0)
- Frontend Next.js + React + TS + Tailwind + shadcn/ui + Lightweight Charts en
  `dashboard/` con **14 pantallas** (Overview, Operations, Market, Strategies,
  Order Flow, ML, Backtesting, Journal, Logs, Health, Reports, Alerts, Settings)
  sobre la API REST/WebSocket; tiempo real por `/ws/events`, tema oscuro, responsive.
- **Capa de comandos del backend**: CORS ampliado a escrituras + `/api/config`
  (whitelist), control de estrategias, `/api/backtesting/run|cancel`,
  `/api/integrations/*` (webhook enmascarado + test), `/api/logs`, `/api/reports/*`,
  `/api/audit`. **Guard anti-live** + **audit log** append-only + config overrides.
- Workspace System, command palette (⌘K), atajos, reportes exportables.
- Pendiente de fase: aplicar overrides en caliente por subsistema; ejecutar
  backtests pesados por HTTP (hoy vía BacktestLab/CLI); verificación con datos
  vivos en `paper`. ADR-058…ADR-062. **Live sigue deshabilitado.**

## Fase 9 — Producción, DevOps y operación 24/7 ✅ (v0.9.0)
- **Live gating**: Live Gate fail-closed (~20 criterios + aprobación humana con
  hash), Safe Mode, Kill Switch persistente y auditado, Recovery (nunca desde
  cero). **Live sigue deshabilitado** (`allow_live=False`, `resolved_mode()→paper`).
- **Documentación viva**: backend Notion real con cola de sincronización en disco
  (`sync_notion()`), bitácora Markdown, mejora continua documentada.
- **Operación**: reportes operativos automáticos hora/día (`send_daily_report()`),
  backups del estado crítico con verificación SHA-256 y restauración
  (`backup_database()`/`restore_database()`), mantenimiento y update manager
  (sin auto-deploy), licencias preparadas (sin restricciones).
- **Observabilidad**: métricas Prometheus + dashboard Grafana provisionado +
  reglas de alerta; Discord multicanal (webhook por canal lógico).
- **Seguridad**: rate limiting + cabeceras defensivas (middleware), validación de
  config, vigilancia de rotación de secretos.
- **Alta disponibilidad**: failover por arriendo de líder primario/standby
  (fail-closed a standby). **Continuous Improvement Engine** (modo desarrollo
  permanente). CI con build de imágenes (despliegue = decisión humana).
- Pendiente de fase: verificación con Notion/Grafana reales; `pg_dump` de la base
  en el runbook de infra; paneles de dashboard frontend dedicados.

## Fase 10 — Quant Research Lab y evolución autónoma ✅ (v0.10.0)
- **Laboratorio independiente** (`app/research/`, fachada `ResearchLab`): descubre,
  valida y promueve estrategias sobre **copias**; **no opera** y **nunca** habilita
  live. Genoma declarativo + compilador a `DecisionSource` (composición auditada,
  sin codegen).
- **Auto Strategy Generator** por reglas (polaridad + afinidad de filtros); **Feature
  Lab** y **Factor Lab** validados por coeficiente de información contra el retorno
  futuro (sin lookahead).
- **Optimización multiobjetivo** (escalarización + frente de Pareto) y **Bayesian
  Lab** (TPE sin dependencias, historial comparable); **Simulation Cluster** concurrente.
- **Candidate Pipeline** (BT→WF→MC→ML→benchmark→riesgo) sobre `BacktestLab`;
  **Ranking Engine**, **Experiment Manager**, **Knowledge Base** y **Candidate Store**
  append-only.
- **Shadow Mode** (comparación estadística de Welch challenger vs vigente, sin
  órdenes ni Decision Engine); **Paper Validation** y **Promotion Manager** fail-closed
  (paper madura + sin drift + supera a la vigente + aprobación del operador).
- `ResearchNotifier` (Discord), documentación a Notion, rutas `/api/research/*`,
  cableado en `bootstrap.py`. ADR-073…082. **Live sigue deshabilitado.**
- Pendiente de fase: pantallas Next.js dedicadas del research dashboard (la API ya
  está); backfill histórico real para correr el laboratorio con datos de mercado;
  habilitar la revisión ML del pipeline cuando haya historial de operaciones.

## Fase 11 (recomendada) — Nivel firma cuantitativa
- Multiagente con IA, optimización distribuida, simulador de order book
  institucional, detección avanzada de anomalías, Execution Cost Analysis,
  portfolio multi-activo con asignación dinámica, API pública de plugins,
  usuarios/roles, app móvil, Kubernetes para HA.

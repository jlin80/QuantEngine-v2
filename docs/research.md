# Quant Research Lab — Fase 10 (investigación y evolución autónoma)

El laboratorio cuantitativo (`app/research/`) **descubre, prueba y valida** nuevas
estrategias automáticamente y sólo promueve las mejores. Es **independiente de
producción**: reutiliza el laboratorio de backtesting (Fase 6) y el ML (Fase 7),
trabaja siempre sobre **copias** (genomas) y **nunca** modifica estrategias en
producción ni habilita live trading.

> **Regla absoluta:** el laboratorio **no opera**. No envía órdenes, no toca
> posiciones ni el Decision Engine. La promoción final **siempre exige aprobación
> humana**. Live sigue deshabilitado (`allow_live=False`, `resolved_mode()→paper`).

## Arquitectura

```
Strategy Generator ──► StrategyGenome (datos) ──► compile_genome ──► DecisionSource
       (reglas)                                                          │
                                                              Candidate Pipeline
                                    Backtesting → Walk Forward → Monte Carlo →
                                    Validación ML → Benchmark → Risk Review
                                                                          │
                              Multi-Objective / Bayesian (TPE)      CandidateReport
                                          │                               │
                                   Ranking Engine ◄───────────── Candidate Store
                                                                          │
                                Paper Validation ──► Promotion Manager (fail-closed)
                                                                          │
   Shadow Mode (challenger ‖ vigente, Welch)         Knowledge Base (append-only)
                                                                          │
                                     eventos ──► ResearchNotifier ──► Discord
                                                              └──► Notion (bitácora)
```

## Componentes

- **Strategy Generator** (`strategy_generator/`): combina 10 bloques de señal
  (EMA/SMA cross, Donchian/ATR breakout, momentum, RSI/VWAP reversion, MACD, delta
  momentum, CVD) y 4 filtros de contexto (sesión, volatilidad, liquidez, régimen)
  con **coherencia de polaridad** y afinidad de filtros. El compilador convierte el
  genoma en una `DecisionSource` sin generar código.
- **Feature Lab / Factor Lab** (`feature_lab/`, `factor_lab/`): 9 features y 13
  factores en 7 familias, validados/rankeados por **coeficiente de información**
  contra el retorno futuro (sin lookahead).
- **Parameter Lab** (`parameter_lab/`): traduce el genoma a un `ParameterSpace` y
  la *source factory* para optimizar sobre copias.
- **Genetic Optimizer multiobjetivo** (`genetic_optimizer/`): escalarización
  ponderada + frente de Pareto. **Bayesian Lab** (`bayesian_lab/`): TPE puro +
  historial comparable.
- **Simulation Cluster** (`simulation_cluster/`): evaluación concurrente acotada.
- **Candidate Pipeline** (`validation_pipeline/`): las 6 etapas obligatorias sobre
  `BacktestLab`.
- **Ranking Engine**, **Experiment Manager**, **Knowledge Base**, **Candidate
  Store**: clasificación y memoria append-only.
- **Shadow Mode** (`shadow_mode/`): comparación estadística de Welch de una
  challenger contra la vigente sobre los mismos datos, sin órdenes.
- **Paper Validation** (`paper_validation/`) y **Promotion Manager**
  (`production_candidate/`): madurez en paper + promoción fail-closed.
- **Report Generator** (`report_generator/`) y **ResearchNotifier**.

## API (`ResearchLab`)

`create_experiment` · `generate_strategy` · `optimize_strategy` ·
`validate_candidate` · `promote_strategy` · `reject_strategy` · `rank_strategies` ·
`generate_feature` · `generate_report` · `archive_experiment`, más
`compare_shadow`, `open_shadow_session`, `start_paper`/`update_paper` y el ciclo
`run_generation_cycle`.

## Endpoints (`/api/research/*`)

`status` · `report` · `catalog` · `candidates` · `experiments` · `knowledge` ·
`ranking` (GET); `generate` · `experiments` · `experiments/{id}/archive` ·
`promote` · `reject` (POST). Observación y ciclo de vida; las corridas pesadas
(validar/optimizar/shadow) van por el `ResearchEngine`/scripts con un dataset.

## Configuración (`settings.research`)

`enabled` (gate; off por defecto), `objective`, `persist`, y sub-secciones
`generator`, `feature_lab`, `factor_lab`, `multi_objective`, `bayesian`,
`simulation`, `pipeline`, `paper`, `shadow`, `promotion`.

## Garantías de seguridad

- No hay ejecución de órdenes ni cambios de estado en producción.
- Genomas = copias; el generador nunca toca las estrategias vivas.
- Stores append-only: el conocimiento nunca se pierde ni se reescribe.
- Promoción **fail-closed**: sin paper madura, con drift, sin batir a la vigente o
  sin aprobación del operador → no promueve, y registra el motivo.
- Live sigue vetado por el Live Gate de la Fase 9.

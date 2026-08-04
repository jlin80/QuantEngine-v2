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

## Presupuesto de CPU del ciclo autónomo (`settings.research.budget`)

> **Estado: propuesta.** `auto_cycle` **sigue en `false` por defecto**. El
> presupuesto está implementado y probado, pero activar el ciclo es una decisión
> explícita del operador tras leer esta sección.

### Por qué hace falta un techo

El laboratorio comparte VPS con el motor que está operando. Un ciclo de
generación no es una tarea: son **cientos de backtests**. El bucle de gestión de
posiciones corre cada 2 s (`manage_interval_seconds`) y es el único componente
que no puede llegar tarde — si se retrasa, un stop se mueve tarde. Sin un techo
explícito, el laboratorio compite con él por CPU.

### Los tres límites duros

| Límite | Valor por defecto | Qué evita |
| --- | --- | --- |
| **Ventana horaria** (`window_start_hour_utc` / `window_end_hour_utc`) | 01:00–05:00 UTC | Correr durante sesión. Es la franja entre el cierre americano (22:00) y la apertura europea (07:00). `start == end` = siempre abierta. |
| **Tope de trabajo** (`max_symbols_per_run`, `max_generated_per_run`) | 2 símbolos × 12 genomas | Que un lote grande se coma la ventana entera. `max_generated_per_run` es la variable que **más multiplica** el número de backtests. |
| **Timeout duro** (`run_timeout_seconds`) | 900 s (15 min) | Que un ciclo colgado siga consumiendo CPU hasta el disparo siguiente. Al vencer se cancela; los candidatos ya registrados se conservan. |

Más dos **vetos de cortesía**, que posponen el ciclo al próximo disparo en vez
de degradarlo:

- `skip_if_positions_open` (por defecto `true`) — el laboratorio puede esperar;
  la gestión de una posición viva, no.
- `skip_if_cpu_pct_above` (por defecto `70 %`) — no arrancar sobre una máquina
  ya cargada. Un sensor que no reporta **no** bloquea (misma regla que Safe
  Mode: no se degrada la operativa por una lectura que falta).

`max_workers` del ciclo (por defecto **2**) queda por debajo de
`simulation.max_workers` (4): en una VPS de 2 vCPU, 4 workers de backtest
compiten directamente con el event loop.

### Consumo estimado de CPU

Estimación de orden de magnitud, **no medida en la VPS** — hay que verificarla
con una ejecución real antes de dejarlo activo:

- 2 símbolos × 12 genomas = **24 pipelines** por ciclo.
- Cada pipeline es BT → WF → MC → ML → benchmark → riesgo sobre `cycle_candles`
  (1 000 velas de 1 m por defecto).
- Con `max_workers=2`, el ciclo satura **~2 de los vCPU disponibles** mientras
  dura, acotado por el timeout de 15 min.
- Frecuencia por defecto: **una vez al día** (`cycle_interval_seconds`).

Es decir: en el peor caso, 15 minutos diarios de 2 vCPU al 100 %, dentro de la
ventana de baja actividad y sin posiciones abiertas.

### ¿Misma VPS u otra máquina? — pros y contras

**Esta decisión queda para el operador**; aquí sólo el análisis.

**Mantenerlo en `qevps` (la que opera)**

- ✅ Cero infraestructura nueva: sin sincronizar datos, sin desplegar dos veces,
  sin un segundo entorno que se desactualiza en silencio.
- ✅ Acceso directo al `MarketDataService` vivo y al Trade Journal, que es de
  donde salen las velas y la evidencia del pipeline.
- ✅ El presupuesto de arriba ya acota el riesgo, y los vetos lo posponen
  cuando la máquina está ocupada.
- ❌ Riesgo residual real: un bug de consumo en el laboratorio afecta a la
  máquina que está operando. El timeout lo acota, no lo elimina.
- ❌ La ventana de 4 h limita cuánto research cabe: escalar el laboratorio
  implica competir con la operativa.

**Moverlo a otra máquina**

- ✅ Aislamiento total: el motor deja de compartir CPU con el laboratorio, y se
  puede investigar sin ventana horaria ni topes agresivos.
- ✅ Permite subir población y símbolos en serio, que es donde el laboratorio
  empieza a rendir.
- ❌ Hay que resolver el acceso a datos: o se replica el histórico, o se expone
  una API, o se comparte almacenamiento. Es la parte cara.
- ❌ Un segundo entorno que mantener, desplegar y vigilar — y que puede quedar
  desalineado con la configuración de producción justo cuando importa.
- ❌ La promoción sigue exigiendo aprobación humana, así que el aislamiento no
  compra seguridad adicional en ese frente: la compra en CPU, no en riesgo de
  operativa.

**Lectura corta:** con el presupuesto aplicado, la misma VPS es razonable para
*empezar a acumular experimentos* y comprobar que el ciclo aporta algo. Mover a
otra máquina tiene sentido cuando el cuello de botella pase a ser el research en
sí (población, símbolos, walk-forwards) y no la falta de experimentos. No hay
motivo para pagar ese coste antes de tener la primera evidencia.

### Cómo activarlo

1. Verificar el consumo real con una ejecución manual del ciclo.
2. `QE_RESEARCH__ENABLED=true` y `QE_RESEARCH__AUTO_CYCLE=true`.
3. Ajustar la ventana a la franja de baja actividad observada.

La promoción de cualquier estrategia generada **sigue exigiendo aprobación
explícita** (PromotionManager fail-closed): nada de esto cambia con el ciclo
activado.

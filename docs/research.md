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

## Activación del ciclo autónomo (`auto_cycle`) — plan gradual

> **Estado: `auto_cycle = false`.** Todo lo de esta sección está implementado y
> probado, pero **el ciclo sigue apagado**. Activarlo es una decisión del
> operador, no del código. Hay un test que lo fija.

### Qué cambió desde el Bloque 6: el incidente del reloj

El Bloque 10 pregunta si el Research Lab pudo contribuir al incidente del
2026-07-31. **No lo causó** —fue el `BacktestLab`— pero la respuesta honesta es
incómoda: **corre sobre el mismo mecanismo**. `ResearchLab` →
`CandidatePipeline` → `BacktestLab`, mismo proceso, mismo event loop, mismo
reloj inyectable.

Con el reloj en un global de módulo (el estado anterior a ADR-091), activar
`auto_cycle` no habría sido neutral: habría pasado la exposición a esa fuga de
"una vez, manual, con alguien mirando" a **una vez al día, automática, a las
02:00 UTC, sin nadie delante**. El incidente tardó 4 días en detectarse con un
disparo manual.

Qué ha cambiado, y por qué ahora es defendible:

- **ADR-091** — el reloj vive en un `ContextVar`: la fuga ya no puede ocurrir.
- **ADR-093** — alarmas de ciego/mudo: si el motor deja de operar por lo que
  sea, se sabe en media hora, no en cuatro días.
- **ADR-095** — guard de arranque: el motor no arranca contaminado.
- **Rollback automático** (abajo) — si el ciclo degrada la operativa, se apaga.

**Recomendación: no activar sin el guard de arranque desplegado.** Es el orden
en el que se implementaron a propósito (Bloque 11 antes que el 10).

### Umbrales de rollback automático

`settings.research.rollback` (activo por defecto — al revés que el ciclo: una
salvaguarda no debería requerir que la enciendan). Si alguno dispara durante un
ciclo, `auto_cycle` pasa a `false`, se publica `ResearchCycleRolledBack` y se
avisa por Discord.

| Disparador | Umbral | Por qué así |
| --- | --- | --- |
| CPU sostenida | >85 % en 3 muestras consecutivas | Un pico durante un ciclo de research **es lo esperado**. Disparar con el primero apagaría la vigilancia en el primer ciclo que hiciera su trabajo. |
| Latencia del bucle de gestión | >2× su propia referencia | Es el único bucle que no puede llegar tarde. Se compara contra su línea base, no contra un absoluto: importa la degradación relativa, no los milisegundos. Requiere ≥30 pasadas — comparar contra una base que no existe fabrica falsos positivos. |
| Desviación del reloj | >5 s | La causa exacta del incidente, vigilada también desde aquí. |
| Alarma de pipeline | `MarketDataBlind` o `SignalDrought` activa | Con el motor sin operar el laboratorio no tiene prioridad. No hace falta demostrar que el research lo causó: apagarlo no cuesta nada. |

**Sólo apaga el laboratorio.** Nunca toca la operativa, no cierra posiciones y
no puede habilitar live. Ante la duda, el que se sacrifica es el research.

**No se rearma solo.** Reactivar es una decisión humana, tras mirar la causa. Un
rollback reversible automáticamente convertiría un problema persistente en un
ciclo de encendido/apagado, más difícil de diagnosticar que el fallo original.

**Es en memoria, no toca el `.env`.** El job lee `auto_cycle` en cada disparo,
así que basta para que no vuelva a entrar. Persistirlo sería que el código se
reescriba la configuración del operador.

### Plan de activación en tres fases

Cada fase tiene un criterio de paso explícito. Lo que se busca en la Fase A no
es que el research produzca algo útil, sino **medir el consumo real** — el dato
que falta desde el Bloque 6, donde el análisis es una estimación de orden de
magnitud, no una medición en la VPS.

| | Presupuesto | Ventana | Duración | Criterio para pasar |
| --- | --- | --- | --- | --- |
| **A** | 1 símbolo × 6 genomas | 02:00-03:00 UTC | 7 días | Ningún rollback disparado; latencia del bucle de gestión sin degradación medible; consumo real dentro de lo estimado. |
| **B** | 2 símbolos × 12 genomas | 01:00-05:00 UTC | 7 días | Igual que A, más: los candidatos generados llegan a `CandidateStore` sin errores. |
| **C** | Presupuesto pleno | 01:00-05:00 UTC | permanente | — |

Ante cualquier rollback, se vuelve a la fase anterior; no se sube hasta
entender qué lo disparó.

**La promoción no cambia en ninguna fase.** `PromotionManager` sigue
fail-closed: ninguna estrategia generada opera contra capital sin tu aprobación
explícita, por muchas fases que se completen.

### Para activar la Fase A

```
QE_RESEARCH__AUTO_CYCLE=true
QE_RESEARCH__BUDGET__MAX_SYMBOLS_PER_RUN=1
QE_RESEARCH__BUDGET__MAX_GENERATED_PER_RUN=6
QE_RESEARCH__BUDGET__WINDOW_START_HOUR_UTC=2
QE_RESEARCH__BUDGET__WINDOW_END_HOUR_UTC=3
```

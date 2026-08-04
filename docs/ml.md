# Machine Learning — Fase 7 (IA y aprendizaje continuo)

La capa de ML aprende del **historial que genera el propio motor** (las
operaciones cerradas del Trade Journal) para **filtrar operaciones malas,
detectar cambios de comportamiento del mercado, ajustar el peso de las
estrategias y detectar degradación** — nunca para predecir el precio.

> **Regla absoluta:** el ML **asesora, no decide**. Ninguna función abre o cierra
> posiciones ni habilita live trading; toda recomendación pasa por el Decision
> Engine y el Risk Manager. El sistema sigue operando **solo en paper trading**.
> Ninguna caja negra: toda predicción es explicable y trazable.

## Arquitectura

```
Trade Journal (Fase 5)  ──►  FeatureEngineer  ──►  Dataset (temporal)
                                                      │
                                          Trainer (holdout + walk-forward)
                                                      │
                                        AutoML  ──►  puerta de validación
                                                      │  (nunca activa uno inferior)
                                              Model Registry (versionado, rollback)
                                                      │
   contexto candidato  ──►  InferenceService  ──►  Prediction explicable  ──►  (asesora)
                                                      │
                              DriftDetector  ·  MetaStrategyManager  ·  AI Advisor
                                                      │
                                    eventos  ──►  MLNotifier  ──►  Discord
```

El `MLEngine` (`app/ml/api.py`) es la fachada única: expone las APIs de la fase y
publica eventos en el Event Bus. No conoce el canal de Discord ni toma decisiones.

## Módulos (`app/ml/`)

- `interfaces/` — contrato `Model` (clasificación binaria de calidad + explicación
  por variable) y `ModelType`.
- `models/` — logística, árbol, random forest, extra trees en **Python puro**;
  XGBoost/LightGBM/CatBoost/redes preparados tras la interfaz; `factory`.
- `ensemble/` — voting (soft/hard/weighted) y stacking.
- `features/` — `FeatureEngineer`: vector fijo y versionado del **contexto de la
  decisión** (hora/sesión, régimen, volatilidad, ATR%, spread, score, confianza,
  R:R, confirmaciones, dirección). El resultado es la **etiqueta**, no una feature.
- `feature_store/` — catálogo profesional versionado (never-overwrite + cómputo
  único con cache).
- `datasets/` — `Dataset` (split/folds temporales, sin barajar) y `DatasetBuilder`.
- `training/` + `evaluation/` — `Trainer` (holdout + walk-forward), métricas puras
  (AUC por rangos, log-loss) y comparación contra el modelo previo.
- `registry/` — `ModelRegistry` versionado, activación reversible (rollback),
  estados, auditoría y persistencia JSON.
- `inference/` + `prediction/` — servicio sobre el modelo activo; `Prediction`
  explicable; degrada con elegancia sin modelo activo.
- `drift/` — `DriftDetector` (feature=PSI / concept / performance / model).
- `auto_ml/` — leaderboard por objetivo; salta backends no instalados.
- `services/` — `StrategyIntelligence` (ranking), `RiskAdvisor`, `AIAdvisor`.
- `meta/` — `MetaStrategyManager` (gobierno de estrategias).
- `optimization/`, `monitoring/`, `experiments/`, `reporting/`, `reinforcement/`.
- `events.py` + `notifications.py` — hitos del ML y su notificador de Discord.

## Ingeniería de features

El esquema es **fijo y versionado** (`FEATURE_SCHEMA_VERSION`) para que
entrenamiento e inferencia produzcan el mismo vector. Las features describen el
contexto conocido al abrir; la etiqueta binaria es el resultado (`win`,
`rr_positive` o `not_stopped`). Nunca se usa el precio directo.

## Qué datos entran al entrenamiento, y por qué

> Esta sección existe para que auditar el ML no obligue a reconstruir el
> razonamiento desde cero. Si cambias lo que entra al training set, **actualiza
> esto en el mismo cambio.**

### El problema

El ML aprende del historial que genera el propio motor. Ese historial arrastra
**bugs de ejecución ya arreglados**: el stop mal calculado (bug de
`contract_size`, pre-27/07), el trailing que apretaba nada más abrir (pre-29/07)
y la salida por régimen que cortaba la tesis antes de tiempo (pre-fix de
familias de régimen).

Entrenar sobre esas operaciones sin distinguirlas no le enseña al modelo *"esta
señal es mala"*. Le enseña *"esta señal es mala **porque la ejecución la
saboteó**"*. El modelo acaba penalizando contextos que en realidad tenían edge:
más torpe, no más inteligente.

### Defensa 1 — segmentación y ponderación por era

Configurable en `ml.data_quality` (`app/ml/datasets/eras.py`). Cada operación se
clasifica en una **era de ejecución** por su **hora de entrada** — una operación
abierta antes de un fix corrió bajo las reglas viejas casi toda su vida, aunque
cerrara después; es la lectura conservadora.

| Era | Hasta | Peso | Por qué |
| --- | --- | --- | --- |
| `pre_contract_size_y_familias_regimen` | 2026-07-27 | **0.0** (excluida) | El stop estaba mal calculado, así que el R de estas operaciones no mide la señal: mide un stop equivocado. No es una muestra floja, es una **medición inválida**. Ponderarla a la baja seguiría metiendo ruido correlacionado. |
| `pre_trailing_activate_r` | 2026-07-29 | **0.35** | El trailing apretaba el stop nada más abrir. El sesgo es real pero **acotado y direccional**, y la entrada y su contexto siguen siendo válidos: se conservan con peso reducido en vez de tirar la muestra. |
| `post_fixes` | — | **1.0** | Historial posterior a todos los fixes conocidos. |

Los pesos por fila viajan en `dataset.metadata["sample_weights"]` y se cortan
junto con las filas en cada split (si no, cada muestra heredaría el peso de
otra). El desglose completo — cuántas operaciones por era, cuáles se excluyeron
y con qué motivo — está en `dataset.metadata["era_breakdown"]` y en
`MLEngine.data_quality_report()`, y sale también en `/api/ml/status`.

**Cuando se arregle el próximo bug de ejecución**, basta añadir una era a la
configuración: no hay fechas ni nombres incrustados en el código.

`ml.data_quality.enabled=false` restaura el comportamiento anterior (todo pesa
igual). Se conserva a propósito, para poder medir el efecto del saneamiento.

### Defensa 2 — dos etiquetas distintas, porque miden cosas distintas

Se entrenan (y se leen) por separado:

| Etiqueta | Qué mide | Qué operaciones usa |
| --- | --- | --- |
| `win` / `rr_positive` / `not_stopped` | **Calidad de ejecución**: qué hizo el motor con la señal, con costes y salidas incluidos. | Todas las de eras admisibles. |
| `signal_quality` | **Calidad de la señal en sí**: ¿la tesis era buena? | Sólo aquellas cuyo cierre **resolvió la tesis**: objetivo, stop, trailing o break-even. |

`signal_quality` **descarta** las operaciones que cerró la ejecución (cambio de
régimen, tiempo, kill switch, manual). Esa operación nunca llegó a poner a
prueba su propia tesis, así que etiquetarla como "señal mala" es exactamente el
error que todo esto pretende evitar. Cada dataset declara qué mide en
`metadata["label_measures"]` (`signal` | `execution`), porque confundirlas es el
fallo, no un detalle.

Juntas responden dos preguntas que no son la misma: **"¿esta estrategia tiene
edge?"** (señal) y **"¿la ejecución está capturando ese edge?"** (ejecución).

### Limitación conocida (pendiente)

La fuente de verdad ideal para la calidad de señal es el **evaluador continuo**
(Fase 4), que resuelve cada señal contra velas futuras con TP/SL/timeout puros,
sin ejecución de por medio. Hoy no se puede unir fila a fila con el Trade
Journal: el evaluador guarda estadística agregada por estrategia, no el
resultado virtual de cada señal, y el `TradeRecord` lleva `decision_id` pero no
los `signal_id` que la originaron.

Por eso `signal_quality` se aproxima **desde el propio journal**, filtrando por
motivo de salida. Es una aproximación honesta y sin lookahead, pero sigue
midiendo operaciones ejecutadas (con sus costes y su slippage).

Cerrar el hueco requiere dos cosas, ninguna hecha todavía:

1. Persistir el resultado virtual por `signal_id` en el `PerformanceTracker`.
2. Propagar los `signal_id` de la decisión hasta el `TradeRecord`.

Mientras tanto, el evaluador continuo **sí** alimenta al Meta Strategy Manager
de forma agregada por estrategia (ver ADR-087).

> ⚠️ **Aviso sobre el histórico:** el evaluador continuo tenía un sesgo de
> medición corregido el 2026-08-03 (ADR-084): resolvía las señales contra la
> vela en curso, cuyo rango incluye precio anterior a la señal. **Las métricas
> virtuales anteriores a esa fecha no son comparables con las posteriores.**

## Registro y puerta de validación

`register_model` nunca sobrescribe (versiona por tipo). Antes de activar,
`evaluate_model` exige mínimos (muestras, AUC, accuracy) sobre holdout +
walk-forward y, si se configura, batir al modelo activo: **nunca se activa un
modelo inferior**. La activación archiva el anterior; `rollback_model` lo restaura
al instante. Incluso aprobado, un modelo sólo se autoactiva si `auto_activate`
está encendido.

## Detección de deriva

Cuatro clases: **feature** (PSI por columna), **concept** (cambio de win rate),
**performance** y **model**. Ante deriva: alerta, **reduce el factor de
confianza** de la inferencia y **programa reentrenamiento** — nunca detiene la
operativa.

## Meta Strategy Manager

Por encima del Strategy Engine y del ML. Puntúa cada estrategia con evidencia
(reciente/histórica/segmentada por activo, sesión y régimen), **sube el peso de
las consistentes** y **desactiva las degradadas** tras `disable_after_periods`
evaluaciones seguidas — todo por **configuración** (activación, prioridad,
ponderación), nunca tocando el código de la estrategia. Guarda auditoría de cada
decisión y recomienda combinaciones nuevas para el laboratorio.

## APIs (`MLEngine`)

`train_model`, `run_auto_ml`, `evaluate_model`, `register_model`,
`activate_model`, `rollback_model`, `predict_trade_quality`, `explain_prediction`,
`assess_risk`, `detect_drift`, `rank_strategies`, `calculate_strategy_weight`,
`recommend_parameters`, `evaluate_meta`; orquestación async
`run_nightly_training` / `run_drift_check` / `run_meta_evaluation`.

## Endpoints del dashboard

Solo lectura salvo las acciones asesoras (nunca operan):

- `GET /api/ml/status` — estado compacto (registro, inferencia, deriva, meta).
- `GET /api/ml/report` — fichas de modelos + ranking + deriva + meta.
- `GET /api/ml/models` — registro versionado + historial de auditoría.
- `GET /api/ml/ranking` — ranking de estrategias por evidencia.
- `GET /api/ml/features` — catálogo del Feature Store.
- `GET /api/ml/meta` — pesos dinámicos y activación de estrategias.
- `POST /api/ml/predict` — predicción explicable para una operación candidata.
- `POST /api/ml/train` — entrenamiento bajo demanda (AutoML → validación).
- `POST /api/ml/drift/check` — chequeo de deriva bajo demanda.
- `POST /api/ml/meta/evaluate` — ciclo de gobierno del Meta Strategy Manager.

## Cableado

`_build_ml` (composition root) construye el `MLEngine` (se entrena con el
`TradeJournal` de la Fase 5) y el `MLNotifier`. El notificador arranca como
servicio; el scheduler registra `ml_nightly_training`, `ml_drift_check` y
`ml_meta_evaluation`. Config `QE_ML__*` (deshabilitado y sin autoactivación por
defecto).

## Pruebas

58 pruebas cubren Feature Store, Model Registry + rollback, entrenamiento y
validación, predicción y explicabilidad, deriva, AutoML, ranking, Meta Manager,
notificador, la fachada de extremo a extremo, los endpoints y el cableado —
incluida la regla de seguridad de que **nunca se activa un modelo inferior**.

## Regla absoluta

El ML nunca abre operaciones por sí solo ni habilita live trading. Toda
recomendación de la IA pasa por el Decision Engine, el Risk Manager y el resto de
validaciones existentes antes de convertirse en una decisión. Solo paper trading.

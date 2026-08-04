# Plan de trabajo — Bloques 8-13 (QuantEngineV2)

Documento previo a escribir código, según pide la tarea: para cada bloque,
diagnóstico verificado contra el repo → propuesta técnica → criterio de
aceptación testeable. Nada de esto está implementado todavía.

Contexto leído: `docs/bitacora.md` completo (hasta "Alarmas de pipeline",
879 tests), `docs/architecture.md` (ADR-001…093, incluidos ADR-091/092/093 del
incidente del reloj y la sección de sizing frente al crecimiento del capital) y
la memoria de Notion **QuantEngine V2** (Fases 1-10 + las sesiones de producción
del 23/07 al 04/08, incluido el cierre de la tarea de Bloques 1-7).

## Reglas que gobiernan todo el plan

- Live deshabilitado; ningún bloque toca el guard anti-live ni `resolved_mode()`.
- Gating de fases: nada de funcionalidad de fases futuras sin aprobación.
- `ruff` + `black` + `mypy --strict` + `pytest` en verde antes de cerrar bloque.
- Sin despliegue a `qevps`. Sin gasto de dinero real.
- Bitácora + CHANGELOG + ADR + memoria de Notion al cerrar cada bloque.

**Ambigüedad detectada en el enunciado (pendiente de tu decisión):** la sección
"Contexto" dice *"Haz commits automáticos"*, y tanto los "Límites duros" como el
"Formato de entrega" dicen *"ningún commit, el repo queda listo pero sin
commitear, siempre"*. Asumo **no commitear** (es la regla histórica del proyecto
y la que se repite dos veces) salvo indicación contraria.

---

## Bloque 8 — Cerrar el loop señal→ejecución (`signal_id` end-to-end)

### Diagnóstico (verificado en código)

La cadena está rota en un punto concreto, y es más corta de arreglar de lo que
sugiere el enunciado:

| Eslabón | Estado hoy |
| --- | --- |
| `StrategySignal.signal_id` | ✅ existe (`app/engine/models/models.py:95`) |
| `Decision.signals_considered` | ✅ ya lleva la tupla de `signal_id` (`decision_engine/engine.py:187`) |
| `DecisionGenerated` (evento del bus) | ❌ **no** transporta los `signal_id`; sólo `decision_id`, `strategy`, `strategy_category` |
| `OrderRequest` / `Order` | ❌ sólo `decision_id` |
| `Position` | ❌ sólo `decision_id` |
| `TradeRecord` | ❌ sólo `decision_id` |
| Resultado virtual por señal | ❌ `PerformanceTracker._resolve` agrega en `StrategyPerformance` y hace `self._open.pop(...)`: **el resultado individual se descarta** |

O sea: hay **dos** huecos reales, exactamente los dos que declaró el Bloque 4 —
(1) el evaluador no persiste resultado por `signal_id`, (2) los `signal_id` no
llegan al `TradeRecord`. El corte del Event Bus está en `DecisionGenerated`.

Consecuencia actual: `app/ml/datasets/builder.py` aproxima `signal_quality`
filtrando por motivo de salida (`eras.py:90`), que mide operaciones ejecutadas,
no la señal.

### Propuesta técnica

1. **Propagación como campo, no como acoplamiento.** Añadir
   `signal_ids: tuple[str, ...] = ()` a `DecisionGenerated`, `OrderRequest`,
   `Position` y `TradeRecord`, poblado desde `Decision.signals_considered` —
   mismo patrón exacto que usó el Bloque 1 con `strategy`/`strategy_category`.
   La ejecución sigue sin conocer el Decision Engine ni los plugins.
   Retrocompatible: default vacío, y `TradeRecord.from_dict` lo lee con
   `data.get(...)` para que el journal antiguo siga releyéndose.
2. **Store de resultados virtuales por señal** (`app/engine/evaluation/`):
   append-only JSONL (`data/engine/virtual_outcomes.jsonl`), una línea por
   señal resuelta con `signal_id`, estrategia, símbolo, dirección, `outcome`
   (tp/sl/timeout), R obtenida, `opened_at`/`closed_at`. Escritura batched, con
   el mismo patrón de rotación/spill que ya usa el Trade Journal. El agregado
   por estrategia **no cambia**: se añade una salida, no se sustituye ninguna.
3. **Join fila a fila** (`app/ml/datasets/join.py`): para cada `TradeRecord`,
   adjuntar los resultados virtuales de sus `signal_ids`. Casos declarados y
   contabilizados en `metadata["join_breakdown"]`, no silenciados:
   - señal con resultado virtual y **sin trade** → la señal no pasó filtros o
     riesgo la vetó; es evidencia de calidad de señal pero no de ejecución.
   - trade **sin** `signal_ids` (journal pre-Bloque 8, posiciones adoptadas del
     broker) → cae al camino antiguo (aproximación por motivo de salida),
     etiquetado como tal en el dataset.
   - trade con `signal_ids` pero **sin resultado virtual** (señal sin niveles,
     o aún abierta en el evaluador) → excluido de `signal_quality`, incluido en
     las etiquetas de ejecución.
4. **Pipeline de entrenamiento**: `signal_quality` pasa a leerse del join real
   cuando hay match, con fallback explícito a la aproximación cuando no lo hay.
   La segmentación y ponderación por era (Bloque 4) se mantiene intacta y se
   aplica **después** del join.
5. **Tests**: mínimo un end-to-end sintético (señal con R virtual conocida →
   decisión → orden → posición → trade → dataset, con el valor intacto), más
   uno por cada uno de los tres casos sin match, más regresión de que un journal
   viejo sin `signal_ids` sigue produciendo dataset válido.
6. **Doc**: `docs/ml.md` — sustituir la nota de "limitación pendiente" por la
   metodología nueva, explicando por qué reemplaza a la aproximación. ADR nuevo.

### Criterio de aceptación

Un test end-to-end demuestra que un trade cualquiera del Trade Journal se traza
hasta su `StrategySignal` original y su resolución virtual; y
`join_breakdown` reporta con números los tres casos sin match, con un test que
exige que no esté vacío (mismo estándar que el `era_breakdown` del Bloque 4).

### Riesgos

Cambiar cuatro dataclasses `frozen`/`slots` toca muchos constructores en tests.
Se mitiga con default vacío. Sin coste en el camino caliente: el store escribe
en batch fuera del worker de datos.

---

## Bloque 9 — Microestructura real para cripto (informe, no migración)

### Diagnóstico

Confirmado en `docs/architecture.md`: "Order flow aproximado — delta/CVD/
microprice se derivan de `buy_volume`/`sell_volume` de la vela; el microprice es
un proxy documentado, no el bid/ask real" (Riesgos Fase 10) y "spoofing/iceberg
son experimentales y de baja confianza por diseño" (Riesgos Fase 4). El Data
Engine (Fase 2) **sí** tiene Binance/Bybit/OKX con WebSocket real, libro
incremental con resync REST y microprice/imbalance/book pressure genuinos
(ADR-013…019). Es decir: la capacidad existe y no se está usando para las
estrategias que más la necesitan.

### Propuesta técnica

Este bloque es **diagnóstico + prototipo**, según pide el enunciado. Entrego un
informe, no producción.

1. **Separar fuente de datos de fuente de ejecución.** Es lo barato y lo que
   recomiendo evaluar primero: el ruteo por símbolo ya existe
   (`market.symbol_provider`), así que alimentar los indicadores de order flow/
   SMC desde `binance:BTCUSDT` mientras las órdenes siguen yendo a
   `mt5:BTCUSDm` es un cambio de configuración de ruteo más un mapeo de símbolo
   equivalente, no un rediseño. Lo que hay que medir y documentar es la
   **desincronización de precio** entre ambas fuentes (CFD vs spot) y su
   latencia relativa.
2. **Ejecución nativa vía API de exchange**: mi lectura previa es que está
   **fuera de alcance** — tocaría Fases 5 y 9, y ninguna cuenta de exchange real
   entra en juego sin tu decisión de capital. Lo documento como opción con
   pros/contras y no lo prototipo.
3. **Cuantificación**: sobre el mismo período histórico, generar señales de
   order flow/SMC con (a) el feed MT5 y (b) libro nativo reconstruido, y
   comparar tasa de falsas señales usando el evaluador continuo como árbitro.
   **Depende de tener histórico de libro nativo**: hoy `data/` local no lo
   tiene, así que la primera tarea del bloque es un backfill de order book de
   Binance (tarea ya listada como pendiente desde la Fase 2).
4. Sección nueva de riesgos en `docs/architecture.md` + recomendación explícita.

### Criterio de aceptación

Informe comparativo con números que permita decidir si migrar la fuente de order
flow es prioritario. Sin código de producción activado, sin ruteo cambiado.

### Riesgo de alcance

El punto 3 es el más caro del bloque entero y depende de datos que no existen
localmente. Si el backfill resulta desproporcionado, entrego el análisis
cualitativo + el prototipo de ruteo dual y te lo digo explícitamente en vez de
inflar la estimación.

---

## Bloque 10 — Activar el Research Lab (`auto_cycle`) — plan, sin activar

### Diagnóstico

`research.auto_cycle = False` (`app/config/settings.py:1541`) y
`ResearchBudgetSettings` (línea 1489) ya implementan ventana 01:00-05:00 UTC,
2 símbolos × 12 genomas, timeout 900 s y los dos vetos (CPU >70 %, posiciones
abiertas). `budget.enabled=false` **deniega**, no relaja. El consumo estimado
—24 pipelines/ciclo, ~15 min de 2 vCPU al 100 %, una vez al día— sigue siendo
una estimación de orden de magnitud, **no medida en la VPS**.

**Cambio relevante desde el Bloque 6 — el incidente del reloj.** Respuesta
directa a la pregunta del enunciado: el Research Lab **no** contribuyó al
incidente (fue el `BacktestLab`), pero **corre exactamente sobre el mismo
mecanismo**: `ResearchLab` → `CandidatePipeline` → `BacktestLab`, mismo proceso,
mismo event loop, mismo reloj inyectable. Con el global de ADR-091, activar
`auto_cycle` habría sido *aumentar la frecuencia con la que se dispara esa
bomba* de una vez manual a una vez al día, sin supervisión, de madrugada.
ADR-091 (ContextVar) elimina la fuga, y el `clock_skew_seconds` la haría visible
en minutos. **Mi lectura: activar `auto_cycle` era claramente imprudente antes
del 04/08 y es defendible después — pero sólo con el guard de arranque del
Bloque 11 en su sitio.** Por eso propongo el orden 11 → 10.

### Propuesta técnica

1. Resumen actualizado en `docs/research.md` con lo anterior.
2. **Activación gradual** en tres escalones, cada uno con criterio de paso:
   - **Fase A (7 días):** 1 símbolo × 6 genomas, ventana 02:00-03:00 UTC. Se
     mide el consumo **real** en la VPS (que es lo que falta), la latencia del
     bucle de gestión de posiciones y el `clock_skew` durante la ventana.
   - **Fase B (7 días):** 2 símbolos × 12 genomas, ventana 01:00-05:00 UTC.
   - **Fase C:** presupuesto pleno, permanente.
3. **Umbrales de rollback automático** (esto es el corazón del criterio de
   aceptación, y hoy no existe): un job que desactive `auto_cycle` solo si
   durante la ventana se cumple cualquiera de — latencia del ciclo de posiciones
   >2× su mediana histórica; CPU sostenida >85 % fuera del veto de arranque;
   `clock_skew_seconds` > umbral de salud; alarma `MarketDataBlind` o
   `SignalDrought` activa. Aviso por Discord en todos los casos.
4. **No se activa nada.** El toggle queda listo, `auto_cycle=false`, con el test
   del Bloque 6 que lo fija intacto. `PromotionManager` fail-closed sin tocar.

### Criterio de aceptación

Plan de activación gradual documentado con los umbrales de rollback escritos y
el desactivador automático implementado y testeado, con `auto_cycle` todavía en
`false` y un test que lo demuestra.

---

## Bloque 11 — Endurecer el despliegue (aislamiento de procesos)

### Diagnóstico — auditoría preliminar de estado compartido

Barrido inicial del repo buscando el patrón del reloj. Hallazgos que merecen
revisión (la auditoría formal es el trabajo del bloque):

| Sitio | Patrón | Valoración preliminar |
| --- | --- | --- |
| `app/backtesting/quant_source.py:53-62` | `_LOOP` global + `threading.Lock` | **El más parecido al del reloj**: estado de proceso instalado por el backtesting. Prioridad 1. |
| `app/logging/recent.py:46-51` | `_buffer` global + `global` statement | Singleton de logging; bajo riesgo funcional, pero es el mismo patrón. |
| `app/brokers/mt5/connection.py:72` | `MetaTrader5` singleton de proceso | Riesgo **inherente a la librería**, no del código: un backtest y el motor vivo comparten la misma conexión MT5. No se puede aislar sin separar procesos. |
| `app/backtesting/clock.py` | ya migrado a ContextVar (ADR-091) | Cerrado. |

Además: los fixtures/monkeypatch de test que sobrevivan al proceso de test son
un riesgo teórico hoy, pero es justo la clase de fallo que costó 4 días.

### Propuesta técnica

1. **Auditoría completa** de globals de módulo mutables, singletons y
   monkeypatching, con tabla de veredictos en `docs/architecture.md`
   (sección "Riesgos de aislamiento de procesos"). Cada entrada: patrón, quién
   lo escribe, quién lo lee, si un backtest puede contaminar al motor vivo.
2. **Guard de arranque fail-fast** (`app/engine/startup_guard.py`) — lo único
   que se implementa de verdad en este bloque:
   - `clock_skew_seconds()` por encima del umbral al arrancar → **abortar**, no
     arrancar degradado.
   - proveedor de tiempo inyectado presente en el contexto de arranque → abortar.
   - `PYTEST_CURRENT_TEST` / `sys.modules` con instrumentación de test en un
     proceso que arranca en `paper`/`production` → abortar.
   - `_LOOP` del `quant_source` ya instalado antes del bootstrap → abortar.
   Fail-fast explícito: **arrancar en silencio con datos corruptos es
   exactamente lo que pasó**.
3. **Propuesta de aislamiento (diseño, sin implementar)**: backtest/research en
   proceso propio (`multiprocessing` con el `BacktestLab` como worker, o un
   contenedor aparte), con el Event Bus como frontera. Documento con coste.
4. **Linux + Docker Compose vs Windows + Tarea Programada**: pros/contras con la
   dependencia dura declarada por delante — **MT5 sólo corre en Windows**, y hoy
   es el broker de la demo. Migrar el motor a Linux implica cambiar de broker o
   correr MT5 bajo Wine; ninguna de las dos es gratis, y ambas tocan tu decisión
   de capital. Alternativa intermedia que voy a proponer: mantener el motor en
   Windows y aislar sólo backtest/research en un proceso hijo.
5. **No se toca `qevps`.**

### Criterio de aceptación

Documento de riesgos de aislamiento en `docs/architecture.md` + guard de
arranque implementado, con un test por cada condición de aborto (y un test de
que un arranque limpio **no** aborta, para que el guard no sea un pie en la
puerta).

---

## Bloque 12 — Criterios de graduación a Live (definir, no aplicar)

### Diagnóstico

Confirmado en "Riesgos conocidos (Fase 5)": *"existe la regla de 'solo tras
criterios estadísticos', pero esos criterios concretos todavía no están escritos
en ningún documento"*. Sigue siendo cierto: no hay un solo lugar con umbrales
numéricos.

### Propuesta técnica

1. Set explícito de criterios, cada uno con su justificación (no números
   redondos por estética): muestra mínima de trades cerrados **post-Bloques
   1-7**, expectancy en R mínima sostenida, profit factor mínimo, drawdown
   máximo tolerado, ventana temporal mínima en paper, y cobertura de N regímenes
   distintos según el `RegimeDetector` de la Fase 3.
2. **Medición del gap contra el journal real** — y aquí hay un bloqueo que
   señalo por adelantado: **el journal de producción no está en este repo**.
   `data/` local sólo contiene `ml/`; el Trade Journal vive en `qevps`, y no
   puedo desplegar ni leer de la VPS. Para cumplir el punto 2 necesito que me
   pases un export de `data/execution/journal.jsonl` (y de
   `data/performance/strategy_stats.json`, el snapshot del evaluador continuo,
   si lo quieres cruzado). **Sin eso implemento el
   calculador del gap y lo dejo listo para correr contra el fichero que me des,
   pero el número real no lo puedo dar.** No lo voy a inventar ni estimar.
3. Documentar explícitamente que cumplir los criterios **no** activa live: el
   guard sigue intacto y la decisión sigue siendo tuya y manual.
4. ADR nuevo (criterios de graduación a Live) para trazabilidad.

### Criterio de aceptación

Documento con los criterios propuestos + herramienta que mide el gap contra un
journal dado, con el gap real calculado **si me facilitas el export**. Cero
cambios de código que toquen el guard anti-live (verificable por diff).

---

## Bloque 13 — Universo de símbolos y sizing a mayor escala

### Diagnóstico

La sección "Sizing y limites de exposicion frente al crecimiento del capital"
(`docs/architecture.md`) ya tiene la tabla de hitos (~$1k / ~$2-5k / ~$20k) y
los cuatro parámetros que se calibraron juntos, más el punto de fondo escrito:
el tope de miles por ciento **no expresa apetito de riesgo, expresa una
restricción de granularidad del broker**. Lo que **falta** frente a lo que pide
el bloque es: la fórmula o lógica detrás de cada hito (hoy son umbrales
afirmados, no derivados) y el checklist accionable.

### Propuesta técnica

1. Derivar cada hito de una fórmula explícita en vez de un número afirmado:
   `max_exposure_pct` mínimo viable = (lote mínimo × contract_size × precio) /
   equity, por símbolo — así el umbral se recalcula solo cuando cambie el
   broker, el símbolo o el precio, en vez de envejecer como constante.
   Tabla por símbolo de la demo Exness (BTCUSDm/ETHUSDm/USTECm + oro, que hoy
   está deshabilitado por contract_size incompatible).
2. Documentar (sin ejecutar) la ganancia en diversificación de un universo
   nativo de exchange frente a las 4 opciones de Exness, enlazado con el
   Bloque 9. **No cambio de bróker ni subo equity: sólo documento.**
3. Checklist "qué revisar cuando suba el capital" en `docs/architecture.md`,
   accionable línea a línea, para no repetir la auditoría manual del 23/07 y
   27/07.

### Criterio de aceptación

Tabla hito de capital → qué config revisar → fórmula/por qué, lista para
consultar, sin ningún cambio operativo aplicado.

---

## Orden de ejecución propuesto

**8 → 11 → 10 → 12 → 13 → 9.**

Razonamiento, donde se desvía del orden del enunciado:

- **11 antes que 10**: activar el ciclo autónomo del Research Lab sin el guard
  de arranque es multiplicar la frecuencia de la clase de fallo que acaba de
  costar 4 días. El propio Bloque 10 pregunta si el Research pudo contribuir al
  incidente; la respuesta honesta hace que este orden sea el único defendible.
- **9 al final**: es el más caro, el que depende de datos que no existen
  localmente (backfill de libro) y el único cuyo entregable es un informe, no
  código. No debe bloquear a los demás.

## Bloques que puedo hacer de forma autónoma vs. los que necesitan tu luz verde

| Bloque | Autónomo | Motivo |
| --- | --- | --- |
| 8 | ✅ | Código interno, sin producción ni capital. |
| 11 | ✅ | Diseño + guard local. La migración a Linux es sólo propuesta. |
| 13 | ✅ | Documentación pura. |
| 10 | ⚠️ parcial | Implemento y documento; **la activación es decisión tuya**. |
| 12 | ⚠️ bloqueado en parte | Necesito el export del journal de producción. |
| 9 | ⚠️ | Requiere decidir si vale la pena el backfill de libro nativo. |

## Lo que necesito de ti antes de empezar

1. **Commits: ¿sí o no?** (contradicción del enunciado, arriba).
2. **Export del Trade Journal de producción** para el gap del Bloque 12.
3. **Confirmación del orden propuesto** (8 → 11 → 10 → 12 → 13 → 9).

# Criterios de graduación a live

> **Fuente única de verdad de los umbrales.** Antes de este documento los
> números vivían repartidos entre la bitácora, `app/production/live/graduation.py`,
> `QualificationCriteriaSettings` y el `PromotionManager` de Fase 10, sin que
> ninguno declarara ser el definitivo.
>
> **Nada de lo que hay aquí activa live.** Cumplir todos los criterios produce un
> informe, no una habilitación: el `LiveGate` y el guard anti-live siguen
> intactos, y el paso a real es una decisión humana, manual y posterior.

## Los tres controles no son versiones del mismo umbral

La pregunta que abrió este documento —«¿los criterios del `PromotionManager` son
los definitivos, o son distintos de los que persigue `graduation_gap.py`?»— tiene
respuesta: **son tres puertas distintas, en tres niveles distintos**, y las tres
tienen que pasarse. No compiten.

| Puerta | Qué decide | Sujeto | Dónde vive |
|---|---|---|---|
| **A. Cualificación** (Fase 6) | Si una estrategia puede llegar a paper | Una estrategia | `QualificationCriteriaSettings` |
| **B. Graduación** (Fase 5) | Si el motor entero es candidato a live | El sistema completo | `app/production/live/graduation.py` |
| **C. Promoción** (Fase 10) | Si un candidato del Research Lab sustituye al vigente | Un modelo/candidato | `PromotionSettings` |

Una estrategia pasa **A** para entrar en paper. El sistema acumula historial en
paper y se mide contra **B**. **C** es ortogonal: gobierna el reemplazo de un
componente por otro mejor, opere el sistema donde opere.

---

## A. Cualificación de estrategia — Fase 6

Umbrales sobre el backtest. Si una estrategia no los supera, el Strategy
Qualification Pipeline la rechaza y no llega a paper.

Configurables por entorno (`QE_BACKTESTING__CRITERIA__*`). Valores por defecto:

| Criterio | Umbral | Ajuste |
|---|---|---|
| Operaciones mínimas | ≥ 30 | `min_trades` |
| Profit factor | ≥ 1.60 | `min_profit_factor` |
| Sharpe | ≥ 1.20 | `min_sharpe` |
| Drawdown máximo | ≤ 12 % | `max_drawdown_pct` |
| Expectativa neta | > 0 | `min_expectancy` |
| SQN | ≥ 2.0 | `min_sqn` |
| Win rate | sin mínimo (0.0) | `min_win_rate` |
| Walk-forward | obligatorio | `require_walk_forward` |
| Monte Carlo | obligatorio | `require_monte_carlo` |
| Supera al benchmark | obligatorio | `require_beat_benchmark` |
| Drawdown en Monte Carlo | ≤ 20 % en el percentil inferior | `monte_carlo_max_drawdown_pct` |
| Escenarios rentables | ≥ 60 % de regímenes | `min_robust_scenarios_pct` |

Los umbrales de A son **más exigentes** que los de B (PF 1.60 vs 1.30, DD 12 %
vs 15 %) a propósito: A mide sobre backtest, donde el sobreajuste es barato; B
mide sobre operaciones reales de paper, donde ya no lo es.

## B. Graduación del sistema a live — Fase 5

Constantes de módulo en `app/production/live/graduation.py`, **no configurables
por entorno** — deliberadamente: un umbral de habilitación de live que se puede
aflojar con una variable de entorno no es un control.

Se miden contra el Trade Journal real con:

```bash
python scripts/graduation_gap.py data/execution/journal.jsonl --since 2026-08-04 --equity 500
```

| Criterio | Umbral | Constante | De dónde sale |
|---|---|---|---|
| Muestra de operaciones cerradas | ≥ 400 | `MIN_TRADES` | Con n=100 y +0.1R el error estándar tapa el resultado; 400 es el orden de magnitud donde una expectativa modesta empieza a ser medible. **Confirmado con dato real (2026-08-21):** la desviación de R medida es **0.710** (no 1.5, que era un supuesto), así que para que el IC excluya el cero con una expectativa verdadera de +0.10R bastan **n ≥ 194**. El umbral de 400 tiene margen de sobra |
| Expectativa por operación | ≥ +0.10R | `MIN_EXPECTANCY_R` | Positiva **con margen**: exigir >0 aprueba un sistema que empata, y un sistema que empata en paper pierde en real (el paper no cobra swaps ni sufre requotes) |
| **IC inferior de la expectativa** | **> 0** | `BOOTSTRAP_RESAMPLES` | Nunca se promueve por una estimación puntual. Con la desviación **medida** de 0.710 y n=400, el IC es de ±0.070: un +0.10R puntual sí excluye el cero, pero por poco — y con la desviación de 1.5R que se supuso al escribir esto, no lo excluiría. El criterio sobrevive a haber corregido el supuesto, que es la prueba que importa |
| **Pliegues walk-forward confirmados** | **≥ 2 de 3** | `MIN_WALK_FORWARD_FOLDS` | Una decisión que no sobrevive a datos que no vio es ajuste al pasado. Un pliegue solo confirma si el in-sample despeja el listón **y** el out-of-sample sale positivo |
| Profit factor | ≥ 1.30 | `MIN_PROFIT_FACTOR` | Por debajo de ~1.2 el resultado lo domina el ruido |
| Drawdown máximo sobre equity pico | ≤ 15 % | `MAX_DRAWDOWN_PCT` | — |
| Días de operativa continuada | ≥ 60 | `MIN_DAYS_IN_PAPER` | El calendario importa aparte de la muestra: 400 operaciones en tres días miden un solo régimen con mucho detalle |
| Regímenes distintos cubiertos | ≥ 3 | `MIN_REGIMES` | Regime Detection de Fase 3 |
| Operaciones por régimen para contarlo | ≥ 30 | `MIN_TRADES_PER_REGIME` | Por debajo, el régimen es anecdótico |
| Salidas forzadas por la ejecución | ≤ 50 % | `MAX_FORCED_EXIT_PCT` | Si el motor cierra la mayoría por régimen/tiempo/kill switch, sus estrategias casi nunca ponen a prueba su propia tesis: lo que se graduaría es la ejecución, no la estrategia |

Cuenta como salida **de tesis** (no forzada) solo: `TAKE_PROFIT`, `STOP_LOSS`,
`TRAILING_STOP`, `BREAK_EVEN`.

> El criterio de salidas forzadas es el que hoy está más lejos de cumplirse.
> La cifra del **91.3 %** de `docs/session_edge.md` es de una configuración
> anterior; medido el 2026-08-21 sobre el tramo con la configuración actual
> (desde el 2026-08-11) son **73.2 %**. Sigue por encima del tope, pero la
> distancia se redujo a la mitad.
>
> ⚠️ **Este criterio está en conflicto con la expectativa, y hay que decidirlo.**
> Barrido de `min_stop_pct` sobre el laboratorio reconciliado:
>
> | piso | expectativa | salidas forzadas |
> |---|---|---|
> | 0.15 (actual) | **−0.0015R** | 76.5 % ❌ |
> | 0.10 | −0.0472R | 53.3 % ❌ |
> | 0.07 | −0.0532R | 40.6 % ✅ |
> | 0.05 | −0.0726R | 31.8 % ✅ |
>
> El criterio **se puede cumplir**, pero sólo empeorando el resultado en cada
> escalón. Se escribió razonando sobre el mecanismo —«si la ejecución cierra la
> mayoría, lo que se graduaría es la ejecución»— y el dato dice que aquí la
> salida por régimen **aporta**: cierra cerca de plano (+0.05R) operaciones que
> con un stop estrecho serían pérdidas de −1R.
>
> Esto también reordena el hallazgo del 2026-07-29 («las señales tienen edge y
> la ejecución lo destruye»): se apoyaba en el evaluador continuo, que resuelve
> contra TP/SL sin salida por régimen. Con el laboratorio reconciliado, el dato
> dice lo contrario.

## Estado medido — 2026-08-21

`python scripts/graduation_gap.py data/execution/journal.jsonl --since 2026-08-11 --equity 500`
sobre 1.737 operaciones, con el laboratorio y los costes ya reconciliados
(commit `4603253`):

| Criterio | Objetivo | Real | |
|---|---|---|---|
| Muestra | ≥ 400 | 1.737 | ✅ |
| Regímenes con ≥ 30 operaciones | ≥ 3 | 4 | ✅ |
| Expectativa | ≥ +0.10R | **−0.001R** | ❌ faltan 0.101R |
| IC inferior | > 0 | −0.034R | ❌ |
| Pliegues walk-forward | ≥ 2 de 3 | **0 de 3** | ❌ |
| Profit factor | ≥ 1.30 | 1.00 | ❌ |
| Drawdown sobre equity pico | ≤ 15 % | 38.1 % | ❌ |
| Días de operativa continuada | ≥ 60 | 10.1 | ❌ |
| Salidas forzadas | ≤ 50 % | 73.2 % | ❌ |

**7 de 9 pendientes.** Los dos que se cumplen son los de muestra y cobertura de
regímenes: hay datos de sobra, y lo que falta es la ventaja, no la evidencia.

Vale la pena leer juntos los dos primeros fallos: la expectativa está en −0.001R
y su intervalo va de −0.034 a +0.032. El sistema **no pierde** de forma medible;
simplemente no gana. Y el walk-forward da 0 de 3 pliegues no porque el
out-of-sample fallara, sino porque el in-sample nunca despejó el listón: no hubo
decisión que validar.

## C. Promoción de candidato — Fase 10

`PromotionManager`, fail-closed: solo promueve si se cumple **todo**.

| Requisito | Valor | Ajuste |
|---|---|---|
| Aprobación explícita del operador | obligatoria | `require_operator_approval` |
| Supera a la vigente | obligatorio | `require_beat_current` |
| Mejora mínima del objetivo | ≥ 0.05 | `min_improvement` |
| Deriva de features (tipo PSI) | ≤ 0.25 | `max_drift` |

Toda decisión queda registrada, se promueva o no.

---

## Huecos: dos cerrados, uno descartado, uno abierto

### ✅ Cerrado — intervalo de confianza en la puerta B

Implementado como criterio `expectancy_ci`: el límite inferior del IC al 95 %
(bootstrap de 10 000 remuestreos, reutilizando `bootstrap_expectancy()` de
`app/backtesting/session_edge.py`) debe estar sobre cero. Se suma al umbral
puntual de +0.10R, no lo sustituye.

### ✅ Cerrado — walk-forward en la puerta B

Implementado como criterio `walk_forward`. El journal se ordena por salida y se
parte en 4 bloques contiguos; para cada frontera, el in-sample es todo lo
anterior y el out-of-sample el bloque siguiente.

Un pliegue **confirma** sólo si el in-sample despeja `MIN_EXPECTANCY_R` (con
esos datos se habría promovido) **y** el out-of-sample sale positivo. Si el
in-sample no despeja, el pliegue no confirma nada: no es un fallo del sistema,
es que no había decisión que validar, y contarlo como éxito premiaría la
ausencia de señal. Es literalmente lo que pasó en agosto de 2026, cuando los
dos pliegues medidos dieron conjunto de selección vacío.

### ❌ Descartado — corte para P(expectativa > 0)

**Es la misma evidencia que el IC, expresada de otra forma.** El bootstrap
devuelve `p_value` = proporción de remuestreos cuya media no es positiva, o sea
`1 − P(exp > 0)`. Exigir un corte aquí *además* de `ci_low > 0` cuenta un solo
hecho dos veces y da falsa sensación de rigor.

Se deja constancia de la decisión en vez de dejarlo como pendiente indefinido.

### ⬜ Abierto — A y B no comparten vocabulario de drawdown

A mide drawdown de backtest sobre capital inicial; B lo mide sobre equity pico
del journal. Los números (12 % y 15 %) no son directamente comparables, así que
el de B no es "más permisivo" que el de A — es otra cosa.

*Pendiente: unificar la base, o documentar por qué son distintas a propósito.*
Es housekeeping: no cambia qué se aprueba.

## Relación con los gaps de investigación

- **Gap 2 (costes) — resuelto al revés de como se creía.** Se temía que la
  expectativa histórica estuviera *sobrestimada* por no cobrar comisiones. Es al
  contrario: Exness **no cobra comisión separada** (801 deals, ninguno con
  `commission != 0`), y quien la cobraba era el `CommissionEngine` del
  laboratorio, a razón de 0.42R por operación. El journal real no está
  sobrestimado; el backtest estaba infravalorado. Corregido en `4603253`.
- **Gap 6 (regímenes) — medido y cerrado.** El criterio de ≥ 3 regímenes se
  cumple (4 con más de 30 operaciones). Pero ninguno tiene edge que sobreviva al
  kill criteria: ver `scripts/regime_edge.py`. Cubrir regímenes y tener ventaja
  en ellos son cosas distintas, y el criterio actual sólo comprueba la primera.
- **Techo de histórico:** los **35 días** que da el terminal MT5 siguen
  limitando cualquier validación sobre periodos de mercado distintos.
- **Sobre el criterio de ≥ 30 operaciones por régimen:** basta para declarar el
  régimen *cubierto*, que es para lo que está. No basta para medir su
  expectativa: con desviación 0.710, n=30 da un error estándar de **0.130R**.
  Conviene no confundir las dos lecturas.

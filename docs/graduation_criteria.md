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
| Muestra de operaciones cerradas | ≥ 400 | `MIN_TRADES` | Con n=100 y +0.1R el error estándar tapa el resultado; 400 es el orden de magnitud donde una expectativa modesta empieza a ser medible |
| Expectativa por operación | ≥ +0.10R | `MIN_EXPECTANCY_R` | Positiva **con margen**: exigir >0 aprueba un sistema que empata, y un sistema que empata en paper pierde en real (el paper no cobra swaps ni sufre requotes) |
| Profit factor | ≥ 1.30 | `MIN_PROFIT_FACTOR` | Por debajo de ~1.2 el resultado lo domina el ruido |
| Drawdown máximo sobre equity pico | ≤ 15 % | `MAX_DRAWDOWN_PCT` | — |
| Días de operativa continuada | ≥ 60 | `MIN_DAYS_IN_PAPER` | El calendario importa aparte de la muestra: 400 operaciones en tres días miden un solo régimen con mucho detalle |
| Regímenes distintos cubiertos | ≥ 3 | `MIN_REGIMES` | Regime Detection de Fase 3 |
| Operaciones por régimen para contarlo | ≥ 30 | `MIN_TRADES_PER_REGIME` | Por debajo, el régimen es anecdótico |
| Salidas forzadas por la ejecución | ≤ 50 % | `MAX_FORCED_EXIT_PCT` | Si el motor cierra la mayoría por régimen/tiempo/kill switch, sus estrategias casi nunca ponen a prueba su propia tesis: lo que se graduaría es la ejecución, no la estrategia |

Cuenta como salida **de tesis** (no forzada) solo: `TAKE_PROFIT`, `STOP_LOSS`,
`TRAILING_STOP`, `BREAK_EVEN`.

> El criterio de salidas forzadas es el que hoy está más lejos de cumplirse:
> las mediciones de agosto de 2026 dan `regime_change` en el **91.3 %** de los
> cierres, casi el doble del tope. Ver `docs/session_edge.md`.

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

## Lo que sigue sin estar definido

Estos huecos son reales y **no** se cierran con este documento; se listan para
que dejen de parecer decididos:

1. **No hay criterio de intervalo de confianza en la puerta B.** El análisis de
   research usa bootstrap al 95 % (`bootstrap_expectancy()` en
   `app/backtesting/session_edge.py`, que devuelve percentil inferior y p-valor),
   pero `graduation.py` compara la expectativa **puntual** contra +0.10R sin
   exigir que el IC inferior esté sobre cero. Con n=400 la diferencia importa.
   *Pendiente: decidir si B incorpora `ci_low > 0` y con qué nivel.*

2. **No hay corte fijado para P(expectativa > 0).** La métrica se calcula en
   research; nunca se fijó el umbral aceptable.

3. **La puerta B no exige walk-forward.** A sí lo exige
   (`require_walk_forward`), pero B —la que decide sobre live— no mira folds.
   *Pendiente: decidir cuántos folds con conjunto de selección no vacío son
   mínimo.* No es hipotético: los dos folds medidos en agosto de 2026 dieron
   conjunto de selección **vacío**.

4. **A y B no comparten vocabulario de drawdown.** A mide drawdown de backtest
   sobre capital inicial; B lo mide sobre equity pico del journal. Los números
   (12 % y 15 %) no son directamente comparables.

## Relación con los gaps de investigación

- Los umbrales de expectativa de B se calcularon sobre operaciones **sin
  comisiones ni slippage real** (gap 2): las comisiones registradas suman 0.00,
  así que cualquier expectativa histórica está sobrestimada.
- El requisito de ≥ 3 regímenes de B choca con el techo de **35 días** de
  histórico de MT5 (gap 6).

# Laboratorio de backtesting (Fase 6)

Backtesting profesional, optimización y validación estadística. Su única
misión es **validar estrategias con evidencia estadística antes de paper
trading** — nada de esto habilita live trading.

Fachada única: `app.backtesting.BacktestLab`.

## Principio rector: un solo motor de ejecución

El backtest **no reimplementa** ejecución, riesgo ni cartera: conduce el mismo
`ExecutionEngine` de la Fase 5 sobre datos históricos. Esto se logra con dos
costuras:

1. **Mercado histórico** (`market.py`): un `MarketStateStore` en memoria que se
   puebla vela a vela, envuelto en un `MarketDataService` real. El motor lee
   `get_ticker()`/`get_candles()` sin saber que es un backtest.
2. **Reloj de replay** (`clock.py` + seam en `app/utils/time.py`): `utc_now()`
   lee de un proveedor inyectable. El backtest instala un `ReplayClock`; el
   tiempo en mercado, las salidas por tiempo y los timestamps reflejan el
   momento histórico. En producción el proveedor es `None` (reloj de pared).

## Flujo por vela

```
para cada vela (en orden cronológico):
    reloj → fin de la vela
    publicar vela cerrada (ATR/estrategias la ven)
    recorrer camino intrabar OHLC → manage_once() en cada punto  (stops/TP/trailing)
    pedir decisión a la DecisionSource (datos <= vela)
    si hay decisión de apertura → process_decision()  (entra al cierre)
    muestrear equity
al terminar: cerrar lo que quede abierto
```

Camino intrabar (ADR-044): `open → extremo adverso → extremo favorable → close`
(mínimo primero en velas alcistas, máximo primero en bajistas). Es una
aproximación conservadora; la reproducción tick-a-tick es estructura futura.

## Módulos

| Módulo | Responsabilidad |
| --- | --- |
| `engine/`, `simulator/`, `market.py` | Motor de backtest que reutiliza el Execution Engine |
| `clock.py` | Reloj de replay |
| `datasets/` | Carga OHLCV (CSV/JSONL) + series sintéticas; dedupe |
| `metrics/` | Estadística extendida (SQN, MAR, Kelly, rachas, exposición...) |
| `optimizer/` | Grid, Random, Genético (funcionales); Bayesiano/Optuna (preparados) |
| `walk_forward/` | Ventanas + análisis IS/OOS |
| `monte_carlo/` | Distribución, drawdown, riesgo de ruina, IC |
| `benchmark/` | Buy&Hold, Random, EMA Cross, VWAP |
| `validation/` | Criterios, detector de sobreoptimización, Qualification Pipeline |
| `experiments/`, `versioning/`, `parameter_sets/` | Registro append-only y versiones |
| `reports/` | JSON / Markdown / HTML (PDF preparado) |
| `replay/` | Control pausar/reanudar/velocidad/avanzar/retroceder |
| `automl/`, `feature_store/` | Estructura preparada (sin entrenar en Fase 6) |
| `notifications.py` | Embeds de Discord del laboratorio |
| `api.py` | Fachada `BacktestLab` |

## APIs de `BacktestLab`

`load_dataset`, `run_backtest`, `calculate_statistics`, `optimize_parameters`,
`run_walk_forward`, `run_monte_carlo`, `qualify_strategy`, `compare_versions`,
`generate_report`, `save_experiment`, `replay_market`, `status`, `criteria`.

## Strategy Qualification Pipeline

Puerta única a paper trading. Corre, en orden: validación técnica → backtest →
métricas mínimas → Monte Carlo → robustez por tramos → comparación contra
benchmarks → walk-forward (si se exige). Devuelve **aprobación o rechazo con
motivos explícitos**. Umbrales en `QE_BACKTEST__CRITERIA__*` (por defecto:
PF ≥ 1.60, Sharpe ≥ 1.20, drawdown ≤ 12%, SQN ≥ 2, expectativa > 0, mínimo 30
operaciones, walk-forward estable, Monte Carlo aprobado, bate benchmark).

## Modelo de coste

Comisiones, slippage y latencia son los mismos motores del paper trading
(Fase 5). El spread se aplica sobre el mid de cada vela (`QE_BACKTEST__DEFAULT_
SPREAD_BPS` si el dataset solo trae OHLCV). Las métricas resultantes son un
límite inferior razonable de lo que ocurriría en real, no un techo optimista.

## Endpoints (solo lectura)

`GET /api/backtesting/status`, `/criteria`, `/experiments`. Lanzar backtests u
optimizaciones (operaciones pesadas) se hace por script/CLI, no por HTTP.

## Estructura preparada (no operativa en Fase 6)

Optimizadores bayesiano/Optuna, reporte PDF, order-book replay, AutoML
(entrenamiento), gestión de parciales, y la conexión del `QuantCore` real como
`DecisionSource`. El Machine Learning es el objetivo de la Fase 7.

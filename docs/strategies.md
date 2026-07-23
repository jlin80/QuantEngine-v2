# Biblioteca de estrategias (Fase 4)

Catálogo de las 20 estrategias de `app/strategies/`. Todas heredan de
`QuantStrategy` (`app/strategies/base/quant_strategy.py`) y por lo tanto:

- **Nunca operan.** Devuelven una `StrategySignal` estructurada (dirección,
  score 0-100, confianza 0-1, Entry/SL/TP, razones, advertencias,
  confirmaciones faltantes, expiración, metadata) que solo el Decision
  Engine puede convertir en decisión.
- **Cero constantes en el código.** Cada parámetro listado abajo es el
  *default* y se sobreescribe por configuración
  (`QE_QUANT__STRATEGIES__<nombre>__PARAMETERS__…`) o, en fases futuras,
  desde el dashboard/optimizador.
- **Explicables siempre.** Sin razones no hay señal; las confirmaciones
  fallidas se reportan como "Falta confirmación de X".
- **Entradas:** exclusivamente el Data Engine vía Feature Store
  (`AnalysisContext`: velas, trades, libro, contexto, régimen). Sin acceso
  a brokers ni a otros módulos.
- **Score:** 5 componentes (calidad, fortaleza, contexto, probabilidad,
  riesgo) con pesos configurables (`score_weights`). **Confianza:** factores
  de volumen, volatilidad, liquidez/spread, régimen, confirmaciones,
  historial reciente y calidad del dato (`confidence_weights`).

Parámetros comunes de la base (heredados por todas): `atr_period`,
`lookback`, `min_candles`, `min_signal_score`, `signal_ttl_seconds`,
`confirmations`, `score_weights`, `confidence_weights`.

---

## trend/

### `anchored_vwap` — AnchoredVWAP
Rebote en el VWAP anclado al último swing low/high de la tendencia.
- **Supuesto:** en tendencia, el AVWAP del último swing actúa de soporte
  dinámico; el precio que lo visita con la tendencia intacta ofrece
  continuación con stop ceñido.
- **Parámetros:** `proximity_atr=0.5`, `stop_buffer_atr=0.75`,
  `risk_reward=2.0` · **Confirmaciones:** volume
- **Regímenes preferidos:** trending
- **Limitación:** necesita swings confirmados; en rango el ancla pierde
  significado.

### `trend_pullback` — TrendPullback
Continuación tras retroceso a la EMA en tendencia.
- **Supuesto:** los retrocesos superficiales a la media en tendencia limpia
  se resuelven a favor de la tendencia.
- **Parámetros:** `ema_period=20`, `proximity_atr=0.5`,
  `stop_buffer_atr=0.5`, `risk_reward=2.0` · **Confirmaciones:** volume
- **Regímenes preferidos:** trending
- **Limitación:** vulnerable a cambios de carácter repentinos (CHOCH) que la
  EMA tarda en reflejar.

## momentum/

### `momentum_continuation` — MomentumContinuation
Continuación tras impulso con pullback superficial.
- **Supuesto:** un impulso ≥ `impulse_min_atr` ATRs seguido de un retroceso
  menor al 50 % suele extenderse (`target_extension` del impulso).
- **Parámetros:** `impulse_max_bars=3`, `impulse_min_atr=1.5`,
  `max_retrace=0.5`, `pullback_bars=4`, `stop_buffer_atr=0.5`,
  `target_extension=0.5` · **Confirmaciones:** delta, volume
- **Regímenes preferidos:** trending, expansion, breakout
- **Limitación:** en noticias el "impulso" puede ser un pico sin
  continuación; el filtro de noticias del Decision Engine lo mitiga.

## orderflow/

### `cvd` — CumulativeVolumeDelta
Divergencia CVD/precio: flujo direccional sin desplazamiento aún.
- **Supuesto:** si el CVD sube con pendiente ≥ `min_cvd_slope` y el precio
  no se ha movido (> `max_price_drift_pct`), hay acumulación agresiva que
  el precio terminará reflejando.
- **Parámetros:** `min_cvd_slope=0.3`, `max_price_drift_pct=0.05`,
  `stop_atr=1.2`, `risk_reward=2.0` · **Confirmaciones:** volume
- **Regímenes preferidos:** ranging, compression
- **Limitación:** CVD derivado de trades públicos agregados (sin tape
  institucional); divergencias largas pueden tardar en resolverse.

### `delta_confirmation` — DeltaConfirmation
Momentum de corto plazo con delta agresor dominante.
- **Supuesto:** cuando ≥ `min_aggression` del volumen reciente es agresor en
  una dirección, el desequilibrio empuja el precio a corto plazo.
- **Parámetros:** `min_aggression=0.65`, `stop_atr=1.0`, `risk_reward=1.5`,
  `signal_ttl_seconds=180` · **Confirmaciones:** volume, spread
- **Regímenes preferidos:** trending, breakout, expansion
- **Limitación:** señal de vida corta (TTL 3 min); sensible a absorción en
  niveles clave.

### `orderbook_imbalance` — OrderBookImbalance
Micro-señal por imbalance + presión del libro alineados.
- **Supuesto:** imbalance ≥ `min_imbalance` y presión ≥ `min_pressure` en la
  misma dirección anticipan el siguiente micro-movimiento.
- **Parámetros:** `min_imbalance=0.25`, `min_pressure=0.2`, `stop_atr=0.6`,
  `risk_reward=1.5`, `signal_ttl_seconds=120` · **Confirmaciones:** delta,
  spread
- **Regímenes preferidos:** (todos)
- **Limitación:** el libro visible puede estar spoofeado — el
  `spoofing_score` experimental del snapshot penaliza la calidad, no la
  elimina.

## smc/

### `liquidity_sweep` — LiquiditySweep
Fade del barrido de un pool de liquidez con rechazo confirmado.
- **Supuesto:** una mecha que barre un pool (equal highs/lows, swing) y
  cierra de vuelta (reclaimed) es stop hunt: el precio revierte hacia el
  lado opuesto.
- **Parámetros:** `recent_bars=3`, `stop_buffer_atr=0.25`, `risk_reward=2.0`
  · **Confirmaciones:** delta, cvd
- **Regímenes preferidos:** ranging, reversal
- **Limitación:** un sweep sin reclaim es breakout, no reversión — el
  detector exige el reclaim, pero rupturas legítimas pueden retestear.

### `order_block` — OrderBlock
Reacción en un order block fresco alineado con la última ruptura.
- **Supuesto:** la última vela contraria antes de un desplazamiento fuerte
  marca órdenes institucionales; su primer retest suele reaccionar.
- **Parámetros:** `allow_mitigated=false`, `stop_buffer_atr=0.5`,
  `risk_reward=2.0` · **Confirmaciones:** delta
- **Regímenes preferidos:** trending, breakout
- **Limitación:** OBs mitigados pierden fiabilidad (excluidos por defecto);
  en choppy market los "desplazamientos" son ruido.

### `fair_value_gap` — FairValueGapStrategy
Mitigación de un FVG abierto alineado con la ruptura vigente.
- **Supuesto:** los gaps de valor (3 velas) actúan de imán/reacción: el
  precio que entra a un FVG ≥ `min_open_fraction` abierto y a favor de la
  estructura reacciona en él.
- **Parámetros:** `min_open_fraction=0.5`, `stop_buffer_atr=0.5`,
  `risk_reward=2.0` · **Confirmaciones:** delta
- **Regímenes preferidos:** trending, breakout
- **Limitación:** FVGs contra la estructura dominante se rellenan sin
  reacción; por eso exige alineación con el último structure break.

### `bos` — BreakOfStructure
Retest del nivel roto por el último BOS.
- **Supuesto:** tras un BOS, el nivel roto cambia de polaridad; el primer
  retest (≤ `retest_atr` ATRs) continúa la tendencia.
- **Parámetros:** `recent_bars=8`, `retest_atr=0.75`, `stop_buffer_atr=0.75`,
  `risk_reward=2.0` · **Confirmaciones:** volume
- **Regímenes preferidos:** trending, breakout
- **Limitación:** BOS por mecha marginal genera retests débiles; el score
  castiga rupturas sin desplazamiento.

### `choch` — ChangeOfCharacter
Entrada temprana tras un CHOCH reciente.
- **Supuesto:** la primera ruptura contra la tendencia previa (CHOCH)
  anticipa el giro; entrar cerca del origen da RR asimétrico.
- **Parámetros:** `recent_bars=5`, `origin_window=10`, `stop_buffer_atr=0.5`,
  `risk_reward=1.5` · **Confirmaciones:** delta, cvd
- **Regímenes preferidos:** reversal, ranging
- **Limitación:** el CHOCH es la señal SMC con más falsos positivos — RR
  conservador y doble confirmación de flujo por diseño.

### `mss` — MarketStructureShift
Reversión sobre un MSS (CHOCH con desplazamiento fuerte).
- **Supuesto:** un CHOCH con desplazamiento ≥ `mss_displacement_atr`
  (definido en el detector) es cambio de régimen con intención: la
  reversión tiene recorrido.
- **Parámetros:** `recent_bars=6`, `origin_window=12`, `stop_buffer_atr=0.5`,
  `risk_reward=2.5` · **Confirmaciones:** delta
- **Regímenes preferidos:** reversal, expansion
- **Limitación:** más raro que el CHOCH simple; en su ausencia no degrada a
  señales menores (eso es del `choch`).

## volume/

### `volume_profile` — VolumeProfileStrategy
Reversión desde VAL/VAH hacia el POC con mecha de rechazo.
- **Supuesto:** en mercado en balance, los extremos del value area rechazan
  (mecha ≥ `min_rejection_wick` del rango de la vela) y el precio rota al
  POC.
- **Parámetros:** `min_rejection_wick=0.35`, `stop_buffer_atr=0.5` ·
  **Confirmaciones:** volume, delta
- **Regímenes preferidos:** ranging, compression
- **Limitación:** en tendencia/expansión el value area migra y los extremos
  no rechazan — de ahí los regímenes preferidos.

## volatility/

### `atr_expansion` — ATRExpansion
Ruptura de volatilidad: expansión del ATR con vela dominante.
- **Supuesto:** `expansion_ratio` ≥ `min_expansion_ratio` con vela de cuerpo
  dominante (≥ `min_body_ratio`) inicia un tramo direccional.
- **Parámetros:** `min_expansion_ratio=1.4`, `min_body_ratio=0.5`,
  `stop_atr=1.0`, `risk_reward=1.5` · **Confirmaciones:** volume
- **Regímenes preferidos:** expansion, breakout, trending
- **Limitación:** las expansiones por noticias pueden revertir íntegras;
  ventanas de noticias vetadas por filtro global.

### `volatility_compression` — VolatilityCompression
Liberación de un squeeze de volatilidad con movimiento medido.
- **Supuesto:** compresión (ratio ATR corto/largo ≤
  `max_compression_ratio`) acumula energía; la ruptura del rango comprimido
  recorre ≈ `measured_move` × altura del rango.
- **Parámetros:** `max_compression_ratio=0.7`, `measured_move=1.0` ·
  **Confirmaciones:** volume
- **Regímenes preferidos:** compression
- **Limitación:** la dirección de la liberación no es predecible ex ante —
  la estrategia espera la ruptura, no la anticipa.

## mean_reversion/

### `mean_reversion` — MeanReversion
Reversión a la media por z-score extremo.
- **Supuesto:** desviaciones ≥ `z_entry` σ de la media de `z_window` velas
  en mercado sin tendencia revierten a la media.
- **Parámetros:** `z_window=40`, `z_entry=2.0`, `stop_buffer_atr=0.75` ·
  **Confirmaciones:** volatility, volume
- **Regímenes preferidos:** ranging, compression
- **Limitación:** en tendencia el z-score se queda extremo ("stay
  irrational"); el encaje de régimen y el filtro de volatilidad son
  imprescindibles.

### `vwap_mean_reversion` — VWAPMeanReversion
Reversión al VWAP de sesión desde una banda extrema.
- **Supuesto:** a ≥ `entry_deviation` σ del VWAP con vela de giro (cuerpo ≥
  `min_reversal_body`), el precio rota de vuelta al VWAP.
- **Parámetros:** `anchor="day"`, `entry_deviation=2.0`,
  `stop_extra_atr=0.75`, `min_reversal_body=0.25`, `min_signal_score=50` ·
  **Confirmaciones:** delta, volume
- **Regímenes preferidos:** ranging, compression
- **Limitación:** en días de tendencia fuerte el VWAP se aleja sin rotación
  (por eso exige la vela de giro y sube su `min_signal_score`).

## breakout/

### `opening_range_breakout` — OpeningRangeBreakout
Primera ruptura del rango de apertura con volumen.
- **Supuesto:** el rango de los primeros `range_minutes` de la sesión define
  el balance inicial; su primera ruptura con volumen ≥ `min_volume_ratio` ×
  media fija la dirección de la sesión.
- **Parámetros:** `range_minutes=30`, `min_volume_ratio=1.2`,
  `target_extension=1.0` · **Confirmaciones:** volume, delta
- **Regímenes preferidos:** breakout, expansion, trending
- **Limitación:** cripto opera 24/7 — la "apertura" es la de la sesión
  configurada en contexto; más significativa para XAUUSD/sesiones RTH.

### `range_breakout` — RangeBreakout
Ruptura del rango previo con validación por volumen y cierre.
- **Supuesto:** la ruptura por CIERRE del rango de `range_lookback` velas,
  con volumen y sin fakeouts recientes (`fake_scan`), continúa ≈
  `target_range_mult` × altura del rango.
- **Parámetros:** `range_lookback=20`, `min_volume_ratio=1.2`,
  `fake_scan=5`, `stop_buffer_atr=0.5`, `target_range_mult=1.0` ·
  **Confirmaciones:** volume, delta
- **Regímenes preferidos:** breakout, expansion, trending
- **Limitación:** rangos poco definidos (sin toques múltiples) dan rupturas
  de baja calidad; el componente `quality` del score lo refleja.

### `vwap_breakout` — VWAPBreakout
Ruptura de la banda 1σ del VWAP de sesión, a favor de la pendiente.
- **Supuesto:** cierre fuera de la banda 1σ con pendiente del VWAP ≥
  `min_slope_pct` en la misma dirección indica desequilibrio sostenido.
- **Parámetros:** `anchor="day"`, `min_slope_pct=0.002`,
  `stop_buffer_atr=0.25`, `risk_reward=2.0` · **Confirmaciones:** volume,
  delta
- **Regímenes preferidos:** trending, breakout, expansion
- **Limitación:** opuesta por diseño a `vwap_mean_reversion` — el consenso
  del Decision Engine resuelve el conflicto según régimen y confianza.

---

## Módulos de soporte

| Módulo | Qué aporta |
| --- | --- |
| `app/analytics/indicators/smc.py` | FVG (% relleno), order blocks (mitigated/breaker), sweeps, equal highs/lows, premium/discount, BOS/CHOCH/MSS, inducement |
| `app/analytics/indicators/structure.py` | Swings, HH/HL/LH/LL, tendencia, consolidación, acumulación/distribución, breakouts válidos y falsos |
| `app/analytics/indicators/orderflow.py` | Delta, CVD, agresores, book pressure, imbalance, absorción, exhaustión, consumo de liquidez, spoofing/iceberg (experimental) |
| `app/analytics/indicators/vwap.py` | VWAP de sesión (día/semana/mes), anchored VWAP, bandas σ, pendiente, distancia % |
| `app/analytics/indicators/volume_profile.py` | POC, VAH/VAL, HVN/LVN, value area por ventana configurable |
| `app/analytics/indicators/atr.py` | ATR clásico/adaptativo, slope, ratio de expansión/compresión |
| `app/analytics/indicators/momentum.py` | ROC, momentum score, aceleración, impulsos |
| `app/analytics/indicators/liquidity.py` | Pools de liquidez, stop hunts, grabs, confirmación de sweep |
| `app/strategies/confirmation/` | Confirmaciones direccionales bajo demanda (delta, cvd, volume, spread, volatility, session, regime, book_imbalance, book_pressure, volume_profile) |
| `app/strategies/shared/api.py` | APIs estables `detect_*` / `calculate_*` (fachada cacheada del Feature Store) |
| `app/engine/evaluation/` | Evaluación continua: operaciones virtuales → win rate, PF, expectativa (R), drawdown, falsas señales, tiempo medio; snapshot JSON + `factor()` |

## Limitaciones generales y próximos desarrollos

- Los defaults NO están calibrados por backtesting; la calibración llega con
  el backtesting histórico y el optimizador (fase ML).
- El order flow se deriva de datos públicos (trades agregados + libro
  visible); no hay tape institucional.
- La evaluación continua resuelve por velas 1m sin slippage/fees: es una
  cota optimista hasta el paper trading.
- Edición de parámetros desde el dashboard: pendiente para la fase de
  frontend (la API de configuración ya lo permite por entorno).

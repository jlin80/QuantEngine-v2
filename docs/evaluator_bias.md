# El sesgo del evaluador continuo, medido

> 2026-08-26. Herramienta: `scripts/evaluator_bias.py`.

## Qué se midió y por qué

El evaluador continuo (Fase 4) resuelve cada señal contra sus propios TP/SL con
las velas posteriores. **No pasa por el Execution Engine**: no hay sizing, ni
spread, ni salida por régimen, ni límites de riesgo.

Que es optimista estaba documentado desde el Bloque 3 —«ignora costes, slippage
y salidas por régimen, así que es optimista de forma sistemática»— pero nunca se
midió **cuánto**, ni si el sesgo es parejo entre estrategias.

Importa porque no es un número informativo: **el Meta Strategy Manager gobierna
los pesos con esa evidencia**, mezclándola con la ejecutada según
`1 - trades/min_trades` mientras la estrategia tiene poca muestra.

## El resultado

XAUUSDM, desde 2026-08-11 (configuración actual), estrategias con ≥ 20
operaciones ejecutadas atribuidas:

| estrategia | señales | virtual | ejecutadas | real | sesgo |
|---|---|---|---|---|---|
| `order_block` | 42 | +1.2034 | 39 | +0.0995 | **+1.1039** |
| `fair_value_gap` | 798 | +0.8464 | 722 | +0.0478 | **+0.7985** |
| `choch` | 254 | +0.4852 | 54 | **−0.0586** | +0.5438 |
| `volatility_compression` | 30 | +0.4104 | 25 | **−0.0673** | +0.4776 |
| `vwap_mean_reversion` | 157 | +0.3320 | 194 | **−0.0992** | +0.4312 |
| `momentum_continuation` | 77 | +0.1525 | 27 | +0.0685 | +0.0839 |
| `bos` | 490 | −0.0020 | 452 | −0.0459 | +0.0438 |
| `mean_reversion` | 266 | −0.1202 | 161 | −0.1524 | +0.0322 |
| `anchored_vwap` | 241 | +0.0107 | 217 | −0.0001 | +0.0107 |
| `atr_expansion` | 129 | −0.0891 | 150 | −0.0208 | **−0.0684** |

- **Sesgo medio +0.3457R**, mediana +0.2576R, desviación 0.3924R.
- **Rango: −0.0684R a +1.1039R.**

## Lo que importa no es la magnitud, es que sea desigual

Un sesgo constante desplaza a todas por igual y **no rompe un ranking**. Este no
lo es: el orden cambia en **8 de 10 posiciones**.

```
Orden VIRTUAL: order_block > fair_value_gap > choch > volatility_compression >
               vwap_mean_reversion > momentum_continuation > anchored_vwap >
               bos > atr_expansion > mean_reversion

Orden REAL:    order_block > momentum_continuation > fair_value_gap >
               anchored_vwap > atr_expansion > bos > choch >
               volatility_compression > vwap_mean_reversion > mean_reversion
```

`choch` y `vwap_mean_reversion` figuran entre las mejores virtualmente y son
**perdedoras reales**. `atr_expansion` figura entre las peores y es de las
mejores. El gobierno de pesos, alimentado por la columna virtual, premia
exactamente lo que la ejecución castiga.

## Por qué el sesgo es tan desigual

La hipótesis que encaja con lo medido: **cuanto más lejos está el objetivo de la
estrategia, más la favorece el evaluador**. El evaluador deja correr la
operación hasta TP o SL; el motor real la corta por régimen en el 74 % de los
casos, antes de que la tesis se resuelva.

Las estrategias de tesis larga (`order_block` espera ~1.950 s, `fair_value_gap`
~880 s) son las que más pierden en la traducción — y son justo las de mayor
sesgo. `atr_expansion`, con ~150 s de tesis, es la única con sesgo negativo.

No está comprobado; es la lectura que el patrón sugiere.

## Consecuencias

1. **El ranking del MSM no es fiable mientras use la evidencia virtual sin
   corregir.** La salvaguarda de que «la virtual nunca desactiva» limita el daño
   a la asignación de peso, no lo elimina.
2. **Las expectativas por estrategia citadas en la bitácora** (`fair_value_gap`
   +1.42R, `order_block` +2.24R y demás) son virtuales. No son lo que esas
   estrategias consiguen al operarse.
3. **`fair_value_gap` corrida sola da −0.0512R** sobre 871 operaciones frente a
   −0.0340R del consenso completo en la misma ventana: aislarla no la mejora.
   Su +0.85R virtual no sobrevive a la ejecución.

## Detalle aparte

`trend_pullback` está apagada en el loader —no emite señales, así que no tiene
evidencia virtual— pero tiene **22 operaciones ejecutadas a +0.3909R**, el mejor
número real de toda la tabla. Con n=22 el error estándar ronda 0.15R, así que no
prueba nada por sí solo. Pero conviene saber **por qué se apagó**: si la decisión
salió de su número virtual, es justo el modo de fallo que este documento
describe.

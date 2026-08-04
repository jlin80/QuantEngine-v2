# Order flow y SMC sobre datos nativos de exchange — informe (Bloque 9)

Informe comparativo pedido por el Bloque 9. **No hay código de producción en
este bloque**: no se cambió el ruteo, no se tocó ningún proveedor y no se activó
nada. El entregable es la decisión informada.

Fecha: 2026-08-04. Datos: journal de producción de `qevps` (1209 operaciones,
23/07 → 04/08) y feed público de Binance.

---

## Resumen ejecutivo

La documentación decía que el order flow estaba *"aproximado"*. **La auditoría
del código dice algo más fuerte: con MT5, la mayor parte no está aproximada,
está ausente.** Y el journal de producción lo confirma sin ambigüedad.

La recomendación cambia en consecuencia. La pregunta no es "¿vale la pena
migrar la fuente de datos para mejorar la precisión?" sino **"¿queremos tener
order flow, sí o no?"** — porque hoy no lo hay.

---

## 1. Qué llega realmente a los indicadores hoy

### El proveedor MT5 no entrega libro ni operaciones

```python
# app/market/providers/mt5.py:39
_CAPABILITIES = frozenset({ChannelType.TICKER, ChannelType.TRADES, ChannelType.CANDLES})
```

`ORDERBOOK` **no está** en las capacidades. Y aunque `TRADES` sí figura, el
bucle de polling (`_poll_once`) sólo construye y emite objetos `Ticker` — nunca
un `Trade`. El pipeline (`TickCollector._process_trade`) sólo alimenta el
historial de operaciones cuando recibe un `Trade` de verdad, así que
`get_recent_trades()` devuelve **lista vacía** para los símbolos de MT5.

Consecuencia directa sobre `OrderFlowSnapshot`:

| Métrica | Con MT5 | Motivo |
| --- | --- | --- |
| `imbalance` | `None` | requiere libro; no hay |
| `book_pressure` | `None` | requiere libro; no hay |
| `spoofing_score` | `0.0` | requiere libro; no hay |
| `iceberg_score` | `0.0` | requiere libro; no hay |
| `consumption` | `None` | requiere profundidad; no hay |
| `delta`, `cvd_series`, `cvd_slope` | vacío / 0 | requieren operaciones; no hay |
| `aggression_ratio` | 0.5 (neutro) | idem |
| `absorption`, `exhaustion` | `""` | idem |

**No es una aproximación de baja calidad: es la ausencia del dato.**

### El volumen de las velas MT5 no es volumen

```python
# app/market/providers/mt5.py — _copy_rates
volume = float(row["tick_volume"])
...
volume=volume,
trades=int(volume),
```

`tick_volume` es el **número de cambios de precio** en la vela, no el tamaño
negociado. El campo `trades` reutiliza ese mismo número. Cualquier indicador que
lea `volume` en un símbolo de MT5 está leyendo actividad de cotización, no
volumen. Además, `buy_volume`/`sell_volume` sólo los rellena el agregador cuando
hay operaciones con lado agresor — con MT5, ambos son 0.

### Confirmación empírica en el journal de producción

De las 1209 operaciones reales, con 130 atribuidas a una estrategia concreta:

> **Cero operaciones de `delta`, `cvd` u `order_book_imbalance`.**

Las tres estrategias puras de order flow de la biblioteca **no han producido ni
una sola entrada en producción**. No es que funcionen mal: no disparan, porque
su entrada es una lista vacía. Las que sí operan (`fair_value_gap`, `bos`,
`mss`, `choch`, `order_block`…) son SMC estructural, que se calcula sobre
OHLC y **no depende del libro** — por eso sí funcionan.

Esto refina el diagnóstico del enunciado: **SMC estructural está bien servido
por MT5; el order flow no está servido en absoluto.**

---

## 2. Desincronización de precio CFD ↔ spot (medida, no estimada)

Pregunta del punto 2: si se alimentan los indicadores con el feed nativo y se
ejecuta en MT5, ¿hay desincronización relevante?

Comparé el precio de entrada real de cada operación del journal contra el cierre
del minuto correspondiente en Binance spot:

| Par | n | Sesgo medio | Desviación mediana | p95 | Máx |
| --- | --- | --- | --- | --- | --- |
| BTCUSDm vs BTCUSDT | 294 | **−11.0 bps** | 10.9 bps | 17.7 bps | 33.9 bps |
| ETHUSDm vs ETHUSDT | 464 | **−9.8 bps** | 9.8 bps | 20.2 bps | 35.4 bps |

**Lectura.** El CFD cotiza sistemáticamente ~10 bps (0,1 %) **por debajo** del
spot. Que la desviación mediana coincida casi exactamente con el sesgo medio
indica que es un **offset estable**, no ruido: casi siempre está en el mismo
lado y con magnitud parecida.

Qué implica, según el uso:

- **Para generar señales de order flow: irrelevante.** Delta, CVD, imbalance y
  absorción son medidas *diferenciales* — un offset constante de precio no las
  altera.
- **Para fijar niveles (entrada/stop/objetivo): sí importa.** Un stop calculado
  sobre precio spot y enviado al CFD queda desplazado ~10 bps de forma
  sistemática, con p95 de 18-20 bps. Con stops de ATR 1m eso es una fracción
  apreciable de la distancia.

**Conclusión operativa:** el modo dual es viable **si y sólo si** se separa
limpio — indicadores de order flow desde el feed nativo, y niveles y ejecución
siempre desde el precio del bróker. Mezclarlos introduce un sesgo sistemático,
no aleatorio, que ningún backtest sobre spot detectaría.

---

## 3. Comparación de tasa de falsas señales — por qué no se hizo

El punto 3 pedía comparar señales de order flow generadas con MT5 frente a
libro nativo reconstruido, mismo período.

**Esa comparación no tiene sentido tal como está planteada, y averiguarlo costó
menos que hacerla.** Con MT5 el order flow no genera señales — el brazo "antes"
del experimento es el conjunto vacío. No hay tasa de falsas señales que
comparar: hay 0 señales.

Lo que sí sería medible es otra cosa: *cuántas señales generaría el order flow
nativo, y con qué calidad*. Pero eso ya no es una comparación, es una
**evaluación desde cero**, y requiere el backfill de libro nivel-a-nivel que
sigue pendiente desde la Fase 2. Antes de pagar ese coste conviene decidir el
punto 5.

No hice el backfill: es la parte cara del bloque y el resultado ya no cambiaría
la recomendación.

---

## 4. Opciones, con coste

### (a) No hacer nada — desactivar las estrategias de order flow

Coste: cero. Ganancia: honestidad en el catálogo. Hoy `delta`, `cvd` y
`order_book_imbalance` figuran como estrategias activas del sistema y votan en
el consenso con señal nula. **No es neutro**: una estrategia que nunca produce
señal sigue ocupando su sitio en la biblioteca y en la documentación, y da la
impresión de una cobertura de mercado que no existe.

### (b) Modo dual — datos nativos, ejecución en MT5

El Data Engine ya lo soporta: los proveedores de Binance/Bybit/OKX están
implementados con WebSocket, libro incremental y resync REST (ADR-013…019), y el
ruteo por símbolo existe (`market.symbol_provider`). Falta:

- Mapear el símbolo equivalente (`BTCUSDM` ↔ `BTCUSDT`) para datos.
- Garantizar que los niveles y la ejecución **nunca** usan el precio nativo
  (sección 2).
- Un backfill de libro para poder validar antes de activarlo.

Coste: medio. Es la opción que el enunciado sugería y sigue siendo razonable.

### (c) Ejecución nativa vía API de exchange — **fuera de alcance**

Tocaría Fases 5 y 9, exigiría una cuenta de exchange real y es una decisión de
capital. No la prototipé y no la recomiendo ahora.

---

## 5. Recomendación

**No es prioritario, y el motivo no es el que esperaba el enunciado.**

El razonamiento no es "el order flow aproximado da señales de peor calidad" —
que era la premisa. Es que **hoy no hay order flow, y el sistema pierde dinero
con las estrategias que sí funcionan**: la expectativa es negativa en todas las
eras (−0.078R global, −0.101R post-Bloque-1) y el 75 % de las salidas las decide
la ejecución, no la tesis (ADR-097).

Añadir una familia de estrategias nueva a un motor cuya ejecución todavía
destruye el edge de las que ya tiene es optimizar el orden equivocado. El order
flow nativo no arregla que el 72 % de las posiciones se cierren por régimen.

**Lo que sí recomiendo hacer ya, y es gratis:** ejecutar la opción (a) —
desactivar `delta`, `cvd` y `order_book_imbalance` mientras la fuente sea MT5, y
documentar en `docs/strategies.md` que requieren un proveedor con libro. Que una
estrategia inerte figure como activa es deuda de honestidad, no de rendimiento.

**Cuándo reabrir esto:** cuando la expectativa sea positiva y estable con las
estrategias actuales. Entonces el order flow nativo pasa de ser una función que
falta a ser una ventaja que sumar — y además el Bloque 13 muestra que el mismo
movimiento resolvería el techo de diversificación (4 símbolos, 2 de ellos
correlacionados) y el problema de granularidad del sizing.

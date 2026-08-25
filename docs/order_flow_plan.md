# Plan de adquisición de order flow real (L2 / orderbook)

> Gap de investigación nº3. Escrito el 2026-08-21 a partir de mediciones sobre
> el terminal de `qevps`, no de supuestos.

## Qué da hoy el venue que se opera

Probado directamente contra el terminal MT5 de Exness, símbolos `XAUUSDm`,
`BTCUSDm` y `ETHUSDm`:

| prueba | resultado |
| --- | --- |
| `market_book_add(symbol)` | **False** en los tres |
| `symbol_info.ticks_bookdepth` | **0** en los tres |
| `copy_ticks_from(..., COPY_TICKS_ALL)` | **funciona**, con `time_msc` |
| campo `last` de esos ticks | **0.0** |
| campos `volume` / `volume_real` | **0** |
| `flags` | 6 = `BID \| ASK` |

**Conclusión medida: el CFD de Exness publica cotizaciones y nada más.** No hay
libro (ni un nivel) y no hay operaciones — el `last` y el volumen vienen a cero,
así que tampoco existe el flujo agregado con el que se calcularían delta o CVD.

Esto confirma por prueba directa lo que la bitácora del 2026-08-04 dedujo del
`_CAPABILITIES` del proveedor: `delta`, `cvd` y `orderbook_imbalance` no están
*aproximados*, están **ausentes**.

## Lo que sí hay, y ya se usa (2026-08-21)

Los ticks llegan con **resolución de milisegundos** (`time_msc`). Con eso se
puede clasificar el agresor por la **regla del tick**, que es el proxy estándar
cuando no hay tape: un quote que sube se atribuye al comprador, uno que baja al
vendedor, y uno que no mueve el precio hereda el lado del anterior.

**Implementado en `CandleAggregator.add_ticker`.** Antes doblaba con
`side=None`, así que `buy_volume` y `sell_volume` quedaban en cero en **todas**
las velas construidas desde quotes — que son todas, porque producción corre con
`aggregate_from_ticks=True`. De ahí que `delta_confirmation` y `cvd` calcularan
siempre 0 y aun así figuraran activas y votando en el consenso.

### Qué mide realmente, y qué no

Mide **presión de cotización**, no agresión ejecutada. El CFD no publica
operaciones, así que no hay agresión real que medir. Es un proxy más débil que
el delta de un tape y no debe leerse como si fuera lo mismo. Está escrito así en
el docstring para que no se confunda dentro de seis meses.

### Consecuencia operativa que hay que vigilar

Este cambio **despierta dos estrategias que llevaban meses mudas**. Pasan de no
emitir ninguna señal a emitir y votar. Sobre un sistema cuya expectativa es
−0.001R y sin validación previa de esas dos, encenderlas y dejarlas operar sería
cambiar el sistema a ciegas.

Por eso se despliegan con `execution.strategies_enabled` en `false` para las
tres: **siguen emitiendo señales y el evaluador continuo las mide, pero no
abren posiciones**. Es exactamente la semántica para la que existe ese toggle
—medir el contrafactual sin arriesgar— y evita el patrón que ya costó caro
antes: dar por bueno un cambio porque desplegó sin errores.

`orderbook_imbalance` es distinto: **no tiene arreglo en este venue**. No hay
libro que aproximar, ni con la regla del tick ni con nada. Queda desactivada y
esa es su situación definitiva mientras el bróker sea Exness.

## Opciones para conseguir L2 de verdad

### A. Binance (cripto), gratis

- **Da:** libro completo por WebSocket, trades reales, histórico. El
  `ProviderRegistry` de la Fase 2 ya tiene el proveedor implementado.
- **Cuesta:** 0 en datos. Pero **operar allí cuesta 0,05-0,10 % por operación**
  frente a los ~0,015 % de spread del CFD: para scalping es **6-13× más caro**
  (ADR-117, sin resolver — falta decidir spot sin cortos vs futuros).
- **Riesgo de base:** medido, el CFD cotiza **~10 bps por debajo** del spot de
  Binance, con offset estable. Irrelevante para señales diferenciales; relevante
  para niveles y ejecución.

### B. L2 de oro por proveedor de datos (COMEX/CME)

- **Da:** libro real del futuro GC, que es donde se forma el precio del oro.
- **Cuesta:** suscripción de datos de mercado (no gratuita) más el trabajo de un
  proveedor nuevo bajo `ProviderRegistry`.
- **Riesgo de base:** se estaría leyendo el libro de un instrumento **distinto
  del que se ejecuta**. El futuro y el CFD se mueven juntos, pero el libro que
  se mira no es el libro contra el que se llena la orden. Esa desconexión es
  exactamente lo que invalida las señales de microestructura.

### C. Cambiar a un bróker que publique profundidad

- **Da:** libro y ejecución en el mismo sitio, que es lo único que hace
  honestas a las señales de microestructura.
- **Cuesta:** cambio de bróker, cuenta nueva, recalibrar spread, comisiones,
  `contract_size` y todo el sizing por símbolo. Es el camino más caro y el único
  sin riesgo de base.

## Recomendación

**No gastar en datos todavía**, por una razón que no es técnica: la expectativa
del sistema no se distingue de cero con la muestra disponible (IC 95 %
[−0,038, +0,034] sobre n=1.499, y harían falta ~77.400 operaciones para resolver
±0,005R). Comprar un feed de L2 para alimentar módulos que no se pueden validar
es pagar por una precisión que no se puede comprobar.

El orden que sí tiene sentido:

1. ✅ **Hecho, en dos mitades.** La regla del tick en el agregador (2026-08-21)
   pobló el volumen firmado de las velas, pero **no bastó**: cuatro días después
   `delta_confirmation` y `cvd` seguían sin emitir una sola señal, porque leen
   la feature `orderflow`, que se construye con `get_recent_trades()` — y MT5
   nunca emite `Trade`. La segunda mitad (2026-08-25) añade
   `FeatureStore._flow_from_candles`, que sintetiza el flujo desde el volumen
   firmado cuando no hay tape. `orderbook_imbalance` queda desactivada, que era
   lo único posible.

   Lección que conviene guardar: poblar el dato no es lo mismo que conectarlo.
   La verificación del 08-21 miró las velas y dio por bueno el arreglo; la que
   valía era mirar si las estrategias emitían.
2. **Medir esas dos con el evaluador continuo**, sin dejarlas operar, y
   aplicarles el mismo kill criteria que se usó para sesiones y regímenes
   (`scripts/regime_edge.py` sirve de plantilla). Si ni siquiera el proxy de
   cotización aporta, el order flow real tampoco va a salvar el enfoque — y esa
   es información barata sobre una decisión cara.
3. **Sólo entonces** evaluar la opción A o C, con la pregunta ya acotada.

La opción B queda descartada salvo que se decida operar el futuro: pagar por el
libro de un instrumento que no se ejecuta acumula el coste de A sin su ventaja.

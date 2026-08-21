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

## Lo que sí hay, y hoy no se usa

Los ticks llegan con **resolución de milisegundos** (`time_msc`). Eso permite
microestructura basada en cotizaciones, que es más débil que el order flow real
pero es medible con lo que ya se tiene:

- intensidad de actualización de cotizaciones (ticks por segundo) como proxy de
  actividad;
- asimetría entre actualizaciones del bid y del ask;
- dinámica del spread (ensanchamientos previos a movimientos);
- retroceso del precio tras rachas de ticks en una dirección.

Ninguno es order flow. Conviene que se llamen por su nombre y no reutilicen las
etiquetas `delta`/`cvd`, precisamente para no repetir el problema actual: tres
módulos que figuran activos y no miden nada.

## Acción inmediata, sin coste ni infraestructura

**Desactivar `delta`, `cvd` y `orderbook_imbalance`.** Están declaradas activas
y votando en el consenso con datos que no existen. Ya figuraba como pendiente
nº2 del 2026-08-04 y sigue sin hacerse. Es la única parte de este plan que no
depende de una decisión de gasto.

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

1. **Desactivar los tres módulos inertes** (gratis, hoy).
2. **Construir los proxies de cotización** con los ticks que ya llegan, y
   medirlos con el mismo kill criteria que se usó para sesiones y regímenes. Si
   ni siquiera esos aportan, el order flow real tampoco va a salvar el enfoque.
3. **Sólo entonces** evaluar la opción A o C, con la pregunta ya acotada.

La opción B queda descartada salvo que se decida operar el futuro: pagar por el
libro de un instrumento que no se ejecuta acumula el coste de A sin su ventaja.

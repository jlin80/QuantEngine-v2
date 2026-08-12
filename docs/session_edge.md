# Edge por sesión en XAUUSD — protocolo, kill criteria y resultados

Este documento se escribe **en dos tiempos a propósito**. Las secciones
«Protocolo» y «Kill criteria» quedaron fijadas **antes de correr el primer
backtest**; los resultados se añaden después y no las reescriben. Si se
cambiara un umbral tras ver un resultado, el experimento dejaría de medir nada:
mediría la voluntad de encontrar algo.

## Pregunta

¿Alguna de las estrategias del catálogo tiene edge **estable** en alguna sesión
concreta de XAUUSD, con suficiente evidencia como para justificar ponderarla por
sesión en el Meta Strategy Manager?

La respuesta admitida por defecto es **no**. El hallazgo del 2026-08-04 —el
ranking entre estrategias es ruido (r = +0.084 entre BTC y ETH)— es la hipótesis
nula de esta tarea, no un obstáculo a superar.

## Datos

| | |
| --- | --- |
| Símbolo | `XAUUSDm` (Exness, el que opera el motor) |
| Fuente | Terminal MT5 local, `copy_rates_from_pos` |
| Resolución | 1m, velas cerradas |
| Tamaño | 50 000 velas — **el techo del terminal**, no una elección |
| Rango | 2026-06-22 → 2026-08-11 (≈35 días de cotización) |
| Reloj | Verificado contra UTC: `symbol_info_tick().time` coincide con `datetime.now(UTC)` al segundo. Sin desfase de servidor que corrija. |

**Limitación declarada por delante:** 35 días es una muestra corta para
particionar en 6 sesiones × ~20 estrategias. La consecuencia esperada es que la
mayoría de celdas salgan como `muestra_insuficiente`, y eso **es un resultado**,
no un fallo de la corrida. Extender la ventana exige subir «Max bars» en el
terminal o una fuente externa de histórico; queda anotado como deuda.

## Sesiones

Se usa la clasificación del Market Context Engine ya existente
(`quant.context.session_hours`), sin reinventarla:

| Celda | Horas UTC | Nota |
| --- | --- | --- |
| `asia` | 00–07 | |
| `asia+europe` | 07–09 | solape |
| `europe` | 09–13 | |
| `america+europe` | 13–16 | solape — el de mayor actividad en oro (el nombre sale de ordenar las sesiones activas, para que la celda sea la misma vengan como vengan) |
| `america` | 16–22 | |
| `off` | 22–24 | ninguna sesión activa |

Los solapes se tratan como **celdas propias**, no se colapsan a la primera
sesión activa. Colapsarlas mezclaría Europa sola con Europa-América, que es
justo la distinción que la tarea pide medir.

> Deuda encontrada al leer el código: `ExecutionEngine._market_view` toma
> `ctx.sessions[0]`, es decir, **se queda con la primera sesión activa y pierde
> el solape**. Este análisis no usa esa ruta (clasifica por la tupla completa),
> pero la asimetría queda registrada.

## Protocolo (fijado antes de medir)

1. **Fidelidad primero.** El backtest debe construir el `ExecutionEngine` con el
   Market Context real, no con `context=None`. Sin eso, la salida por régimen
   —el 72 % de los cierres en producción— no existe en el laboratorio y las
   celdas no serían comparables con lo que opera.
2. **Una estrategia sola por corrida**, las demás desactivadas (mismo método que
   `scripts/strategy_edge.py`), sobre el mismo tramo de velas.
3. **Atribución por sesión en el momento de la entrada**, no del cierre: la
   sesión es una condición de la decisión, y etiquetar por el cierre metería
   información posterior a la señal.
4. **Dos poblaciones contadas por separado** en cada celda: señales resueltas por
   el evaluador continuo (`n_señales`) y operaciones ejecutadas (`n_trades`). No
   son la misma cosa y confundirlas es el fallo clásico.
5. **Bootstrap** (10 000 remuestreos) para el intervalo de confianza de la
   expectancy. El número puntual no se reporta solo.
6. **Corrección por comparaciones múltiples** sobre el conjunto entero de celdas
   evaluadas, con Benjamini-Hochberg (FDR) a q = 0.10. Con ~20 × 6 celdas, varias
   parecerán ganadoras por azar si no se corrige.
7. **Estabilidad**: el histórico se parte en **3 sub-periodos contiguos de igual
   tamaño**, definidos por fecha antes de mirar ningún resultado. La ventaja
   tiene que repetirse de signo en los tres.
8. **Detector de sobreoptimización** (Fase 6) sobre las celdas supervivientes.

## Kill criteria (fijados antes de medir)

### Umbral de muestra

Una celda con **`n_trades` < 30** se reporta como `muestra_insuficiente`. No se
omite de la tabla, no se rellena con el promedio global y no entra en el conteo
de comparaciones múltiples. Con menos de 30 operaciones el intervalo de
confianza de la expectancy es más ancho que cualquier efecto que se pudiera
detectar; llamar a eso «edge» sería fabricar una cifra.

Motivación del 30 y no de otro número: es el mínimo con el que el bootstrap
sobre esta distribución deja de estar dominado por dos o tres colas, y es
coherente con el ≥10 usado el 04/08 —más exigente, porque aquí se hacen ~120
comparaciones en vez de 40.

### Qué cuenta como «edge estable» en una celda

Las cuatro condiciones, **todas**:

1. `n_trades` ≥ 30 y `n_señales` ≥ 30.
2. Expectancy en R > 0 con el **límite inferior** del IC bootstrap al 95 % > 0.
3. Sobrevive a la corrección FDR (q = 0.10) sobre el conjunto de celdas elegibles.
4. Expectancy > 0 en **los tres** sub-periodos (signo, no magnitud).

Una celda que cumple 1-2 pero no 3-4 se reporta como **`inestable`** — edge
presente pero indistinguible de ruido. Es una categoría de salida, no un
descarte silencioso.

### Cuándo se declara «no hay edge estable por sesión»

Si **cero celdas** cumplen las cuatro condiciones. En ese caso no se cablea
ningún peso por sesión, y el entregable es la tabla completa.

### Cuándo vale la pena cablear pesos por sesión

Hace falta un mínimo de **3 celdas con edge estable, repartidas en al menos 2
sesiones distintas**.

El porqué de ese mínimo: con una sola celda superviviente sobre ~120
comparaciones, el resultado esperado bajo la hipótesis nula con FDR q=0.10 es
precisamente ≈1 falso positivo. Una celda no es señal, es el ruido que la
corrección admite por diseño. Exigir 2 sesiones distintas evita el otro modo de
fallo: tres estrategias correlacionadas disparando en la misma franja horaria,
que es una observación, no tres.

Si se supera el mínimo pero las celdas ganadoras son todas de la misma sesión, el
resultado se reporta como **«hay señal en una franja, insuficiente para
segmentar»** y tampoco se cablean pesos.

### Qué NO se hace pase lo que pase

- No se activa `governance` activo (`QE_ML__META__APPLY_GOVERNANCE` queda en
  `false`) sin aprobación explícita del operador.
- No se activa ni desactiva ninguna estrategia.
- No se sube ningún límite de riesgo, no se toca `allow_live` ni `auto_cycle`.
- No se calibran parámetros de celdas sin edge estable: sería ajustar ruido.

## El hallazgo que apareció al intentar medir: el freno no se suelta

**La primera corrida completa no midió edge por sesión. Midió un freno.**

17 estrategias × 50 000 velas produjeron entre 5 y 18 operaciones cada una, y
las 97 celdas salieron como `muestra_insuficiente`. Eso no es un resultado sobre
sesiones: es una muestra que no existe. Diagnóstico con el `RiskManager` real
instrumentado, una estrategia (`bos`) sobre 10 000 velas:

| | |
| --- | --- |
| Decisiones aceptadas por el QuantCore | **601**, repartidas por los 8 días |
| Operaciones abiertas | **8**, todas del primer día |
| Rechazos por `max_consecutive_losses` | **573** |
| Rechazos por `max_positions_per_symbol` | 19 |

### Por qué no vuelve a abrir nunca

`max_consecutive_losses = 5` es un **estado absorbente**, no un freno temporal:

- `evaluate_entry` bloquea toda entrada cuando el contador llega a 5;
- el contador **sólo se reinicia con una operación ganadora**
  (`on_trade_closed` con `net_pnl > 0`);
- sin poder abrir, no puede haber ganadora.

No hay decaimiento por tiempo, ni ventana móvil, ni reinicio diario — al
contrario que el cortacircuitos, que sí se suelta solo al pasar su ventana.

**Y sobrevive a los reinicios.** `RecoveryService` persiste
`consecutive_losses` en el snapshot y lo restaura al arrancar
(`recovery/service.py:104` y `:207`). Apagar y encender el motor no lo limpia.

### Por qué importa fuera del laboratorio

Es el mismo cuadro clínico del incidente del 2026-08-04 —el motor vivo, sano,
conectado y sin operar— con otra causa. Las alarmas de motor mudo (ADR-093) lo
detectarían hoy en media hora, pero reportarían «no hay decisiones»: el motivo
real quedaría a dos saltos de distancia.

Cinco pérdidas seguidas no son un evento raro con una expectativa cercana a cero
y un win rate en torno al 45 %: son lo esperable cada pocas decenas de
operaciones. Merece una revisión aparte de esta tarea.

**Lo que se hizo aquí, y por qué no es mover la portería.** El barrido se
repitió con `max_consecutive_losses = 0` —su valor «apagado», que sí está bien
guardado con `> 0`— y **sólo en el laboratorio**, con la bandera explícita
`--lift-loss-streak-halt` registrada en el JSON de salida. Los umbrales del kill
criteria no se tocaron: siguen siendo los de arriba, escritos antes de ver nada.
Lo que cambió no es el listón, es que ahora hay algo que medir. **En producción
no se ha cambiado ningún límite de riesgo.**

## Exclusiones antes de analizar (Bloque 2)

### Order flow que MT5 no publica

`cvd`, `delta_confirmation` y `orderbook_imbalance` quedan **fuera del análisis
por sesión**. La razón no es su rendimiento: es que no tienen con qué disparar.
MT5 no expone libro (`_CAPABILITIES` sin `ORDERBOOK`) y el polling nunca emite
`Trade`; dieron 0 operaciones en BTC y en ETH, y 0 en producción. Segmentar por
sesión una estrategia que no produce señales es repartir ruido en seis cajas.

Quedan **marcadas como candidatas a desactivar**, que es lo que ya estaba
anotado el 04/08. **Esta tarea no las activa ni las desactiva**: apagar una
estrategia es una decisión del operador.

### Auditoría del patrón «0 significa máximo, no apagado»

El patrón encontrado en `max_daily_loss_pct` **no era un caso aislado**.
Verificado ejecutando el `RiskManager` real con cada parámetro a `0`, cuenta
recién abierta y ninguna operación cerrada:

| Parámetro | Con `0` | Lectura |
| --- | --- | --- |
| `max_daily_loss_pct` | **bloquea toda entrada desde el arranque** | El límite queda en 0 y la comparación es `realized <= limit`; con `realized = 0.0` (ninguna operación cerrada) ya se cumple. No es «para al primer céntimo perdido»: es «no abre nunca». |
| `max_weekly_loss_pct` | idem | mismo código, mismo efecto |
| `max_monthly_loss_pct` | idem | mismo código, mismo efecto |
| `circuit_breaker_loss_pct` | **corta tras la primera operación cerrada**, incluso con PnL 0.00 | Comprobado: `pnl=-0.01` y `pnl=0.00` disparan el cortacircuitos |
| `max_spread_bps` | rechaza cualquier spread > 0 | es decir, todo |
| `max_exposure_pct`, `max_symbol_exposure_pct`, `max_correlation_exposure_pct` | bloquean toda entrada | aquí «0 % de exposición» sí es una lectura defendible |
| `max_open_positions`, `max_positions_per_symbol` | bloquean toda entrada | «cero posiciones» también es defendible |
| `max_consecutive_losses` | **apagado** (`> 0` explícito) | ejemplo de cómo debería estar el resto |
| `kill_switch_drawdown_pct` | **apagado** (`> 0` explícito) | idem |
| `min_liquidity` | **apagado** (`> 0` explícito) | idem |

**Lo que hace peligroso a este grupo no es el bloqueo, es su silencio.** Los tres
límites de pérdida y el cortacircuitos no fallan ruidosamente: el motor sigue
vivo, sano y conectado, y simplemente no abre nada. Es el mismo cuadro clínico
del incidente del 04/08 —cuatro días sin operar sin que nada avisara— con otra
causa. Hoy las alarmas de motor mudo (ADR-093) lo detectarían en media hora,
pero el motivo que reportarían sería «no hay decisiones», no «alguien puso un
límite a 0».

**No se corrige en esta tarea.** El arreglo (guardar con `> 0` los tres límites
de pérdida, el cortacircuitos y `max_spread_bps`) toca el camino del Risk
Manager y cambia el significado de una configuración existente: si alguien puso
un `0` a propósito para frenar el motor, el «arreglo» lo reanudaría. Es una
decisión del operador, y queda propuesta con su diff mental hecho.

## Resultados (2026-08-11)

Corrida: 17 estrategias aisladas × 50 000 velas 1m de `XAUUSDm`, spread real
0.6 bps, freno de racha levantado sólo en laboratorio.
**11 575 operaciones simuladas y 38 849 señales resueltas** por el evaluador
continuo. Datos completos en `data/backtesting/session_edge_xauusd.json`.

### Veredicto: `no_hay_edge_estable_por_sesion`

| Categoría | Celdas |
| --- | --- |
| edge estable | **0** |
| edge presente pero inestable | 0 |
| sin edge | 68 |
| muestra insuficiente | 29 |

No hizo falta que la corrección FDR descartara nada: **ninguna celda llegó
siquiera a tener expectancy positiva.** La mejor de las 68 elegibles es
`order_block` en el solape América-Europa, con **−0.058R**. No se cablea ningún
peso por sesión, y los Bloques 4 y 5 no se ejecutan — calibrar parámetros o
proponer multiplicadores sobre esto sería ajustar ruido, que es exactamente lo
que el kill criteria existía para impedir.

### La sesión no es la variable que importa

La expectancy mediana por sesión, sobre celdas elegibles:

| Sesión | Celdas | Expectancy ejecutada | Expectancy de señal | Ops |
| --- | --- | --- | --- | --- |
| america | 14 | −0.213 | +0.052 | 2 382 |
| america+europe | 13 | −0.213 | +0.127 | 1 651 |
| europe | 14 | −0.219 | −0.002 | 2 263 |
| asia+europe | 11 | −0.242 | −0.038 | 1 010 |
| asia | 16 | −0.244 | +0.124 | 4 088 |

Todo el rango entre la mejor y la peor sesión es **0.031R**. El hueco entre lo
que da la señal y lo que consigue la ejecución es **0.256R** — ocho veces mayor.
Segmentar por sesión sería repartir mejor un problema que no está ahí.

### El hallazgo real: la señal no es el problema, la ejecución sí

| | |
| --- | --- |
| Celdas con expectancy **ejecutada** > 0 | **0 de 68** |
| Celdas con expectancy **de señal** > 0 | **42 de 68** |
| Celdas con señal positiva **y** ejecución negativa | **42** |
| Hueco mediano (ejecución − señal) | **−0.2565R** |

Ese −0.2565R coincide casi exactamente con el −0.252R que ADR-097 midió sobre
operaciones reales por otra vía. Dos mediciones independientes, el mismo número.

**Y ahora se puede decir dónde se va.** Es lo que el cableado del Bloque 1
desbloqueó: con `context=None` esta salida no existía en el backtest y el
desglose de abajo era literalmente imposible de obtener.

Mezcla de motivos de salida (tres estrategias representativas, misma serie):

| Estrategia | Ops | `regime_change` | R medio de esa salida | `take_profit` | R medio | Holding mediano |
| --- | --- | --- | --- | --- | --- | --- |
| `fair_value_gap` | 1 499 | **92.7 %** | −0.184 | 1.8 % | +1.528 | 240 s |
| `range_breakout` | 1 541 | **90.0 %** | −0.184 | 5.3 % | +1.529 | 180 s |
| `order_block` | 282 | **93.3 %** | −0.190 | 3.9 % | +1.784 | 180 s |

Los niveles propios de las estrategias se comportan de forma sana: cuando una
operación llega a su objetivo devuelve **+1.5R**, y cuando llega a su stop pierde
**−1.3R**. El problema es que **casi nunca llegan a ninguno de los dos**: la
salida por cambio de régimen cierra nueve de cada diez operaciones a los tres o
cuatro minutos, con −0.18R cada vez. Multiplicado por el 92 % de la muestra, eso
**es** el hueco de 0.256R.

### Lo que esto NO prueba

- **No prueba que la salida por régimen deba quitarse.** Existe para proteger, y
  medir su coste no mide lo que evitó. Lo que sí queda demostrado es que su
  configuración actual —en 1m, con un holding mínimo de 2-4 minutos— cierra la
  inmensa mayoría de las operaciones antes de que su tesis se resuelva.
- **No prueba que las estrategias tengan edge.** Que 42 celdas den señal positiva
  en 35 días de un solo símbolo no sobrevive por sí solo a la lección del
  ranking BTC/ETH. Está sin someter a walk-forward ni a corrección múltiple,
  porque el kill criteria de esta tarea se declaró sobre la expectancy
  **ejecutada**, no sobre la de señal. Cambiarlo ahora sería mover la portería.
- **No es un resultado sobre live.** Todo esto es backtest sobre 35 días.

### Deuda anotada por el camino

- **35 días es el techo del terminal**, no una elección. Subir «Max bars» en MT5
  o traer histórico externo es el requisito para cualquier conclusión más fuerte.
- `ExecutionEngine._market_view` se queda con `ctx.sessions[0]` y pierde el
  solape en el modelo de slippage. El journal ya guarda la tupla completa
  (`entry_sessions`); esa ruta no.
- El freno por racha de pérdidas, arriba. Es lo primero de la lista.

## Cierre de los Bloques 1 y 7 (2026-08-12)

Dos casillas del enunciado quedaron sin cerrar el 11/08: el **walk-forward
IS→OOS** del Bloque 1 (se probó estabilidad por sub-periodos, que no es lo
mismo) y la **verificación de régimen de scalping** del Bloque 7, que nunca se
escribió. Se cierran aquí con datos, no con argumentos. Corrida nueva sobre
50 000 velas de `XAUUSDm` (2026-06-23 → 2026-08-12), mismo protocolo y mismos
umbrales — no se tocó ninguno.

### Bloque 1 — walk-forward de la regla de selección

Un walk-forward valida **una decisión tomada con datos pasados**. Aquí la
decisión es la del kill criteria: *«esta celda tiene edge»*. Cada pliegue la
aplica usando sólo las operaciones anteriores a la frontera (in-sample) y
después mide, **sin volver a elegir**, qué hicieron esas mismas celdas en el
bloque siguiente (out-of-sample). Es exactamente la separación que faltó cuando
el ranking BTC/ETH resultó ser ruido (r = +0.084): allí se eligió y se midió
sobre el mismo tramo.

| Pliegue | Fin del IS | Celdas elegibles IS | Seleccionadas | Ops OOS | Expectancy OOS |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-07-10 | 44 | **0** | 0 | — |
| 2 | 2026-07-26 | 56 | **0** | 0 | — |

**La regla no seleccionó nada que validar, en ninguno de los dos pliegues.** No
es que el out-of-sample saliera mal: es que ni siquiera mirando **sólo** el
in-sample —el tramo donde una regla sobreajustada tendría todas las de ganar—
hubo una celda con expectancy positiva, IC inferior sobre cero y superviviente
del FDR. Un conjunto de selección vacío es el resultado más fuerte que puede dar
un walk-forward: no hay nada que pueda degradarse fuera de muestra porque no hay
nada elegido dentro.

Esto **confirma** el veredicto del Bloque 3 por una vía independiente y cierra
la casilla: no se cablean pesos por sesión.

### Bloque 7 — ¿esto sigue siendo scalping?

Se mide en dos direcciones, porque una sola daría por buena la mitad del
problema. Techo declarado: **1800 s (30 min)**, muy por debajo de la salida por
tiempo del motor (240 min) — lo que se comprueba es que la operativa siga siendo
intradía corta, no que respete el tope duro.

| | |
| --- | --- |
| Celdas medidas | 68 |
| Holding mediano | **240 s** (rango de medianas: 180-360 s) |
| Celdas por encima del techo | **0** |
| Celdas con edge que sólo aparece con holdings largos | **0** |
| Celdas cuya salida dominante **no** resuelve su tesis | **68 de 68** |

**Veredicto: `dentro_del_regimen_de_scalping`.** Ninguna combinación
(estrategia, sesión) se sale del régimen, y la pregunta del enunciado —«¿el edge
aparece sólo con holdings largos?»— no llega a plantearse, porque no hay edge en
ninguna celda.

Pero la medición **por abajo** es la que importa. Un holding corto no prueba que
el sistema haga scalping: puede probar que algo lo está cortando. Reparto real
de motivos de salida sobre las 11 370 operaciones de las celdas medidas:

| Motivo de salida | Cuota |
| --- | --- |
| `regime_change` | **91.3 %** |
| `stop_loss` | 5.7 % |
| `take_profit` | 2.3 % |
| `trailing_stop` | 0.5 % |
| `break_even` | 0.2 % |
| `time_exit` | 0.1 % |

**En las 68 celdas, sin una sola excepción, la salida dominante es
`regime_change`.** El 11/08 esto se vio en tres estrategias representativas;
ahora se sabe que es universal. Sólo el **8.5 %** de las operaciones termina en
un nivel propio de la estrategia (objetivo, stop o trailing). El sistema no está
haciendo scalping *por diseño de sus estrategias*: lo está haciendo porque la
salida por régimen cierra nueve de cada diez posiciones a los cuatro minutos.

Sigue sin probar que la salida por régimen deba quitarse —medir su coste no mide
lo que evitó—, pero el Bloque 7 queda respondido: **el régimen es de scalping, y
no es la estrategia quien lo decide.**

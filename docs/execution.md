# Execution Engine — Fase 5 (Paper Trading)

Capa de ejecución profesional del Quant Engine V2. **Regla de oro: solo paper
trading.** Ninguna orden llega a un broker real; el objetivo es validar el
sistema en condiciones realistas (spread, slippage, latencia, comisiones,
rechazos) sin arriesgar capital. El modo `live` se fuerza a `paper`.

## Arquitectura del flujo de órdenes

Ninguna estrategia puede enviar órdenes. El único disparador es una decisión
**aceptada** del Decision Engine (`DecisionGenerated`):

```
Market Data (bid/ask, velas)
        │
Strategy Engine → Decision Engine ──DecisionGenerated──►  Execution Engine
                                                                │
                                        ┌───────────────────────┼───────────────────────┐
                                        ▼                       ▼                       ▼
                                  Market Context           Position Sizer           Risk Manager
                                  (ATR/spread/régimen)     (riesgo→cantidad)      (límites/filtros/
                                                                                   kill switch/CB)
                                        │                                                │
                                        └──────────────► OrderRequest ◄─────────────────┘
                                                                │
                                                          Paper Engine
                                             (spread + slippage + latencia +
                                              comisión + rechazo + parcial + gap)
                                                                │  Fill
                                        ┌───────────────────────┼───────────────────────┐
                                        ▼                       ▼                       ▼
                                 Position Manager        Portfolio Manager         Trade Journal
                                 (SL/TP/trailing/BE)     (balance/equity/DD)      (registro total)
                                        │
                                        └──────► Eventos ──► Execution Notifier ──► Discord
```

En paralelo, un **bucle de gestión** (`manage_interval_seconds`) valora las
posiciones abiertas, aplica break-even y trailing, y las cierra por stop,
objetivo, tiempo o cambio de régimen. Los cierres **nunca** se rechazan ni se
ejecutan parciales: una salida jamás debe quedar atascada.

## Módulos (`app/execution/`)

| Módulo | Responsabilidad |
| --- | --- |
| `models/` | Órdenes, fills, posiciones, snapshot de cartera, registro de trade, enums. |
| `events.py` | Eventos de ejecución (JSON-safe) publicados en el bus. |
| `commission/` | Comisiones por nocional/unidad/fijo, maker/taker, overrides por símbolo. |
| `slippage/` | Slippage dinámico por volatilidad, liquidez, tamaño, sesión y tipo de orden. |
| `latency/` | Latencia red+broker+exchange+interna con jitter y deriva de precio. |
| `sizing/` | Position sizing: fijo, %, ATR, riesgo fijo, riesgo dinámico, Kelly parcial. |
| `paper_engine/` | Simulador de ejecución de alta fidelidad (produce `Fill`). |
| `order_manager/` | Ciclo de vida de órdenes + estructura OCO (preparada). |
| `position_manager/` | Posiciones abiertas/cerradas, SL/TP, trailing, break-even, salidas. |
| `portfolio_manager/` | Contabilidad de margen: balance, equity, capital, drawdown, exposición. |
| `risk_manager/` | Límites duros, filtros de mercado, kill switch y circuit breaker. |
| `journal/` | Registro append-only (memoria + JSON Lines) de cada operación. |
| `performance/` | Métricas de desempeño derivadas del journal. |
| `notifications/` | Traducción de eventos a embeds de Discord (servicio desacoplado). |
| `execution_engine/` | Orquestador (`Service`): entrada, gestión y salida. |
| `api.py` | `ExecutionCore`: fachada de lectura para el dashboard. |

## Modelo de riesgo

El Risk Manager es la última barrera antes de ejecutar. Una entrada se aprueba
solo si supera **todos** los límites, evaluados en orden:

1. Kill switch / circuit breaker inactivos.
2. Nocional positivo.
3. Pérdidas consecutivas por debajo del máximo.
4. Nº de posiciones (total y por símbolo).
5. Pérdida realizada diaria/semanal/mensual dentro de límite.
6. Exposición total / por símbolo / por grupo de correlación.

Filtros de mercado adicionales: **spread** (`max_spread_bps`) y **liquidez**
(`min_liquidity`). Cortacircuitos:

- **Kill switch**: se activa cuando el drawdown sobre el equity pico supera
  `kill_switch_drawdown_pct`; bloquea nuevas entradas y **aplana** las
  posiciones abiertas.
- **Circuit breaker**: se activa cuando la pérdida realizada en la ventana
  `circuit_breaker_window_minutes` supera `circuit_breaker_loss_pct`; pausa
  las entradas hasta que la ventana expira.

## Contabilidad (Portfolio Manager)

Modelo de margen, simétrico para largos y cortos:

- `balance` = caja realizada (solo cambia por comisiones y PnL bruto al cerrar).
- `equity` = `balance` + PnL flotante de las posiciones abiertas.
- `used_capital` = Σ nocional de entrada / apalancamiento.
- `free_capital` = `equity` − `used_capital`.
- `drawdown_pct` = caída desde el equity pico.

## Position sizing

Método configurable (`QE_EXECUTION__SIZING__METHOD`): `fixed_amount`,
`percent`, `atr`, `fixed_risk`, `dynamic_risk` (ajustado por la confianza de la
decisión) y `kelly` (parcial, con respaldo a riesgo fijo sin historial). La
distancia de stop se deriva del ATR (`atr_stop_multiplier`); el objetivo, de la
relación `reward_risk`. Todo con tope de exposición por operación
(`max_position_pct`).

## Formato de notificaciones Discord

Servicio desacoplado por eventos (`ExecutionNotifier`): se suscribe al bus y
traduce cada evento a un **embed** con color por severidad. Campos típicos:
Activo, Dirección, Cantidad, Entrada/Salida, SL, TP, PnL, R, Motivo, Tiempo e
ID. Eventos notificados: posición abierta/cerrada, break-even, trailing, orden
rechazada, riesgo, kill switch y circuit breaker. Reportes periódicos
(horario/diario) con equity, drawdown, exposición, win rate, profit factor y
expectativa.

## Endpoints del dashboard

- `GET /api/execution/status` — estado completo del motor.
- `GET /api/execution/portfolio` — balance, equity, drawdown, exposición.
- `GET /api/execution/positions` — abiertas y cerradas.
- `GET /api/execution/trades` — historial del Trade Journal.
- `GET /api/execution/performance` — métricas de desempeño.
- `GET /api/execution/risk` — límites, kill switch, circuit breaker.
- `GET /api/execution/report` — reporte combinado.
- `GET /api/execution/notifications` — últimas notificaciones enviadas.

## Pruebas

`tests/unit/test_execution_*.py` (43 pruebas): simulación (comisión/slippage/
latencia), sizing, paper engine, managers (portfolio/position/order), riesgo,
performance, journal, notificaciones (canal falso) y el flujo extremo a extremo
(entrada → gestión → salida con publicación de eventos). Todo con componentes
reales y RNG sembrado para determinismo.

## Regla absoluta

No se habilita el live trading. Ninguna orden se envía a un broker real. Todo
el flujo se ejecuta exclusivamente en paper trading, validando que el sistema
opere de forma estable durante largos periodos antes de permitir cualquier
operación con dinero real.

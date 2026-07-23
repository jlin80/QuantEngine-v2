# Modo DEMO — trading en cuenta Exness demo vía MetaTrader 5

Este documento describe el adaptador MT5 que permite a QuantEngine V2 **enviar
órdenes reales a una cuenta demo** de Exness (u otro broker MT5), manteniendo el
trading con **dinero real (`live`) duro-bloqueado**.

## Modelo de seguridad

`ExecutionMode` tiene tres valores:

| Modo    | Qué hace                                            | Cómo se habilita |
|---------|-----------------------------------------------------|------------------|
| `paper` | Simulador `PaperBroker`, sin broker externo.        | Por defecto.     |
| `demo`  | Órdenes **reales** a una cuenta **demo** (sin dinero real). | `QE_EXECUTION__MODE=demo` + credenciales MT5. |
| `live`  | Dinero real. **Prohibido.**                         | Nunca por config: exige el Live Gate + aprobación humana (aún sin adaptador). |

`ExecutionSettings.resolved_mode()` devuelve `demo` sólo cuando el modo es `demo`;
cualquier otra cosa (incluido `live`) cae a `paper`. `select_broker()` construye
el `MT5Broker` en `demo` y **falla ruidosamente** si se pide `live`.

## Componentes

- `app/brokers/mt5/connection.py` — `MT5Connection`: init/login/shutdown/healthcheck
  del terminal (paquete `MetaTrader5`, sólo Windows), thread-safe (lock de proceso).
- `app/brokers/mt5/broker.py` — `MT5Broker` (implementa `ExecutionBroker`): manda
  `order_send` a mercado, `quantity`=**lotes** cuantizados al `volume_min/max/step`
  del símbolo, buy@ask / sell@bid, SL/TP, traduce retcodes a `BrokerExecution`.
- `app/market/providers/mt5.py` — `MT5MarketProvider` (implementa
  `MarketDataProvider`): **polling** de ticks (`symbol_info_tick`) y velas
  (`copy_rates_from_pos`). Comparte la `MT5Connection` con el broker (un único
  terminal, un único lock). Su bajo volumen evita saturar el Event Bus.
- Wiring en `app/engine/bootstrap.py` (`_maybe_build_mt5`): construye UNA
  `MT5Connection` compartida cuando el modo es `demo`/broker `mt5_exness`, y la
  inyecta en el feed (`registry.register("mt5", …)`) y en el broker
  (`select_broker(..., mt5_connection=…)`).

## Requisitos en el servidor (Windows)

1. **Terminal MetaTrader 5 instalado** y **logueado en la cuenta demo Exness**
   (Archivo → Login → cuenta/servidor). Debe permanecer abierto.
2. Paquete Python en el venv de v2: `pip install MetaTrader5`.
3. Habilitar *Algo Trading* en el terminal (botón verde) — si no, `order_send`
   devuelve `TRADE_RETCODE_TRADE_DISABLED`.

## Configuración (`.env`)

```dotenv
QE_EXECUTION__MODE=demo
QE_EXECUTION__ENABLED=true
# El balance lo lee de la cuenta demo MT5 (recargable en Exness). INITIAL_BALANCE
# sólo se usa como fallback si el terminal no responde.
QE_EXECUTION__USE_BROKER_BALANCE=true
QE_EXECUTION__INITIAL_BALANCE=1000

QE_BROKER__NAME=mt5_exness
QE_BROKER__MT5__LOGIN=<login_demo>
QE_BROKER__MT5__PASSWORD=<password_demo>
QE_BROKER__MT5__SERVER=<servidor_exness>   # p. ej. Exness-MT5Trial8

# Feed de datos por MT5 (FX/metales) en vez del firehose de Binance:
QE_MARKET__DEFAULT_PROVIDER=mt5
QE_MARKET__SYMBOLS=["XAUUSD"]
QE_MARKET__CHANNELS=["ticker"]
```

> **Credenciales:** las escribe el operador directamente en el `.env` del
> servidor. Nunca se commitean ni se registran en logs (`SecretStr`).

## Notas operativas

- `quantity` se interpreta como **lotes** MT5. El sizing de v2 se calibró para
  cripto; revisa `QE_EXECUTION__SIZING__*` para FX/metales antes de confiar en el
  tamaño de posición.
- Empezar con **XAUUSD** (oro): es el edge rentable histórico del proyecto.
- El feed MT5 es local (polling): si el terminal se desconecta, `healthcheck()`
  pasa a `False` y el broker rechaza con `BROKER_UNAVAILABLE`.

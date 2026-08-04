"""WebSocket que retransmite los eventos del bus al dashboard en tiempo real."""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.container import Container
from app.core.events.base import Event
from app.core.events.bus import EventBus

router = APIRouter()
_log = logging.getLogger("app.api")

# Eventos de mercado y analítica de altísima frecuencia (cientos por segundo):
# inundarían el stream del dashboard sin aportar información accionable. El
# dashboard consume esos datos por REST/paneles dedicados, no por el feed de
# eventos. Se filtran aquí, en origen, para que el "event stream" muestre sólo
# hitos con sentido (señales, decisiones, ejecución, ML, research, sistema).
_HIGH_FREQUENCY_EVENTS: frozenset[str] = frozenset(
    {
        # Data Engine (por tick / trade / libro / vela)
        "NewTick",
        "TradeReceived",
        "TickerUpdated",
        "PriceUpdated",
        "OrderBookUpdated",
        "OrderBookResyncRequired",
        "CandleClosed",
        "FundingUpdated",
        "OpenInterestUpdated",
        "LiquidationReceived",
        # Analítica por-barra (detecciones continuas)
        "LiquidityDetected",
        "FVGDetected",
        "OrderBlockDetected",
        "VWAPCalculated",
        "DeltaCalculated",
        "CVDCalculated",
        "VolumeProfileUpdated",
        "MomentumDetected",
        "StrategyScoreUpdated",
    }
)


@router.websocket("/ws/events")
async def events_stream(websocket: WebSocket) -> None:
    """Stream meaningful bus events to the connected client as JSON.

    Los eventos de mercado/analítica de alta frecuencia (:data:`_HIGH_FREQUENCY_EVENTS`)
    se omiten para no inundar el dashboard. El cliente solo recibe; no se
    aceptan comandos entrantes por este canal.
    """
    await websocket.accept()
    container: Container | None = websocket.app.state.container
    if container is None or not container.contains(EventBus):
        await websocket.close(code=1013, reason="Event bus not available")
        return

    bus = container.resolve(EventBus)
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)

    async def forward(event: Event) -> None:
        if event.name in _HIGH_FREQUENCY_EVENTS:
            return  # ruido de alta frecuencia: no llega al dashboard
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)

    subscription = bus.subscribe(forward)

    async def pump() -> None:
        """Push queued events until the client goes away."""
        while True:
            event = await queue.get()
            if websocket.client_state is not WebSocketState.CONNECTED:
                return
            await websocket.send_json(event.to_dict())

    # El envío va en una tarea aparte y la corrutina del endpoint se queda
    # escuchando al cliente. Es la única señal **fiable** de que se fue: este
    # canal es de sólo lectura para el cliente, así que `receive` sólo retorna
    # cuando llega el `websocket.disconnect`.
    #
    # Antes el endpoint era el bucle de envío y sólo salía con
    # `WebSocketDisconnect`. Cuando el cliente desaparece sin cierre limpio
    # (pestaña cerrada, red caída, dashboard reiniciado) `send_json` **no
    # lanza**: asyncio ve el transporte con `_conn_lost`, descarta el envío en
    # silencio y vuelve. El bucle seguía consumiendo eventos y "enviándolos" a
    # un socket muerto indefinidamente — un WARNING de asyncio por cada evento
    # del motor, y una suscripción al bus que nunca se liberaba. Cada recarga
    # del dashboard dejaba otro zombi sumando ruido.
    pump_task = asyncio.create_task(pump())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        _log.debug("WebSocket client disconnected")
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump_task
        bus.unsubscribe(subscription)

"""WebSocket que retransmite los eventos del bus al dashboard en tiempo real."""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.to_dict())
    except WebSocketDisconnect:
        _log.debug("WebSocket client disconnected")
    finally:
        bus.unsubscribe(subscription)

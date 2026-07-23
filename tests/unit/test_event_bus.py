"""Pruebas del Event Bus: suscripción, despacho, aislamiento de errores."""

import asyncio

import pytest
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.events.events import PriceUpdated, SignalCreated
from app.core.exceptions import EventBusError
from app.utils.time import utc_now


def _price(symbol: str = "XAUUSD") -> PriceUpdated:
    return PriceUpdated(symbol=symbol, bid=2400.0, ask=2400.5, timestamp=utc_now())


async def test_publish_and_dispatch():
    bus = EventBus()
    await bus.start()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(handler, PriceUpdated)
    await bus.publish(_price())
    await asyncio.sleep(0.05)
    await bus.stop()

    assert len(received) == 1
    assert isinstance(received[0], PriceUpdated)
    assert received[0].symbol == "XAUUSD"


async def test_wildcard_subscription_receives_everything():
    bus = EventBus()
    await bus.start()
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(event.name)

    bus.subscribe(handler)  # comodín
    await bus.publish(_price())
    await bus.publish(
        SignalCreated(strategy="s1", symbol="BTCUSD", direction="long", confidence=0.7)
    )
    await asyncio.sleep(0.05)
    await bus.stop()

    assert received == ["PriceUpdated", "SignalCreated"]


async def test_handler_error_is_isolated():
    bus = EventBus()
    await bus.start()
    received: list[Event] = []
    error_calls: list[str] = []

    async def broken(event: Event) -> None:
        raise RuntimeError("boom")

    async def healthy(event: Event) -> None:
        received.append(event)

    bus.add_error_callback(lambda name, exc: error_calls.append(name))
    bus.subscribe(broken, PriceUpdated)
    bus.subscribe(healthy, PriceUpdated)
    await bus.publish(_price())
    await asyncio.sleep(0.05)

    assert len(received) == 1, "el handler sano debe ejecutarse pese al roto"
    assert bus.stats.handler_errors == 1
    assert len(bus.dead_letters) == 1
    assert error_calls, "el callback de error debe dispararse"
    await bus.stop()


async def test_publish_when_stopped_raises():
    bus = EventBus()
    with pytest.raises(EventBusError):
        await bus.publish(_price())


async def test_unsubscribe():
    bus = EventBus()
    await bus.start()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    sub = bus.subscribe(handler, PriceUpdated)
    bus.unsubscribe(sub)
    await bus.publish(_price())
    await asyncio.sleep(0.05)
    await bus.stop()

    assert received == []


async def test_event_serialization():
    event = _price("BTCUSD")
    payload = event.to_dict()
    assert payload["event"] == "PriceUpdated"
    assert payload["symbol"] == "BTCUSD"
    assert "event_id" in payload and "occurred_at" in payload

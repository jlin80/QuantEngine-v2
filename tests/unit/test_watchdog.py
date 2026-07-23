"""Pruebas del watchdog: congelamiento, reinicio automático y presupuesto."""

import asyncio

import pytest
from app.config.settings import WatchdogSettings
from app.core.events.bus import EventBus
from app.core.events.events import ModuleFrozen
from app.core.exceptions import WatchdogError
from app.monitoring.watchdog import ComponentStatus, Watchdog


def _settings() -> WatchdogSettings:
    return WatchdogSettings(
        check_interval_seconds=0.03,
        default_heartbeat_timeout_seconds=0.05,
        max_restarts=1,
        error_threshold=3,
        error_window_seconds=10,
    )


async def test_frozen_component_is_restarted():
    bus = EventBus()
    await bus.start()
    frozen_events: list[ModuleFrozen] = []

    async def collector(event):
        if isinstance(event, ModuleFrozen):
            frozen_events.append(event)

    bus.subscribe(collector, ModuleFrozen)

    watchdog = Watchdog(_settings(), bus)
    restarts = {"n": 0}

    async def restart() -> None:
        restarts["n"] += 1

    watchdog.register("worker", restart_callback=restart)
    await watchdog.start()
    await asyncio.sleep(0.15)  # sin heartbeats -> se congela
    await watchdog.stop()
    await asyncio.sleep(0.05)
    await bus.stop()

    assert restarts["n"] >= 1, "debe intentar el reinicio automático"
    assert frozen_events, "debe publicar ModuleFrozen"
    assert frozen_events[0].module == "worker"


async def test_heartbeat_prevents_freeze():
    bus = EventBus()
    await bus.start()
    watchdog = Watchdog(_settings(), bus)
    watchdog.register("alive")
    await watchdog.start()

    for _ in range(5):
        watchdog.heartbeat("alive")
        await asyncio.sleep(0.03)

    assert watchdog.component_statuses["alive"] is ComponentStatus.OK
    await watchdog.stop()
    await bus.stop()


async def test_restart_budget_exhausted():
    bus = EventBus()
    await bus.start()
    watchdog = Watchdog(_settings(), bus)  # max_restarts=1

    async def restart() -> None:
        pass  # no reanuda heartbeats: volverá a congelarse

    watchdog.register("flaky", restart_callback=restart)
    await watchdog.start()
    await asyncio.sleep(0.3)
    await watchdog.stop()
    await bus.stop()

    assert watchdog.component_statuses["flaky"] is ComponentStatus.EXHAUSTED


async def test_unknown_component_rejected():
    bus = EventBus()
    watchdog = Watchdog(_settings(), bus)
    with pytest.raises(WatchdogError):
        watchdog.heartbeat("ghost")


async def test_duplicate_registration_rejected():
    bus = EventBus()
    watchdog = Watchdog(_settings(), bus)
    watchdog.register("a")
    with pytest.raises(WatchdogError):
        watchdog.register("a")

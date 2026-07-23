"""Servicio de notificaciones de ejecución (con canal falso, sin red)."""

import asyncio

from app.core.events.bus import EventBus
from app.execution.events import KillSwitchTriggered, PositionClosed, PositionOpened
from app.execution.notifications import ExecutionNotifier
from app.notifications.models import Notification
from app.notifications.service import NotificationService


class _RecordingChannel:
    """Canal de notificaciones que sólo memoriza lo recibido."""

    def __init__(self) -> None:
        self.received: list[Notification] = []

    @property
    def channel_name(self) -> str:
        return "recording"

    async def send(self, notification: Notification) -> None:
        self.received.append(notification)

    async def close(self) -> None:
        return None


async def _wired() -> tuple[EventBus, NotificationService, _RecordingChannel, ExecutionNotifier]:
    bus = EventBus()
    await bus.start()
    channel = _RecordingChannel()
    notifications = NotificationService()
    notifications.register_channel(channel)
    await notifications.start()
    notifier = ExecutionNotifier(notifications, bus)
    await notifier.start()
    return bus, notifications, channel, notifier


async def test_position_events_become_embeds():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(
        PositionOpened(
            source="t",
            position_id="p1",
            symbol="BTCUSDT",
            side="long",
            quantity=1.0,
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=104.0,
        )
    )
    await bus.publish(
        PositionClosed(
            source="t",
            position_id="p1",
            symbol="BTCUSDT",
            side="long",
            quantity=1.0,
            exit_price=104.0,
            pnl=4.0,
            r_multiple=2.0,
            exit_reason="take_profit",
            holding_seconds=120.0,
        )
    )
    await asyncio.sleep(0.05)
    await notifier.stop()
    await notifications.stop()
    await bus.stop()

    titles = [n.title for n in channel.received]
    assert any("Posición abierta" in t for t in titles)
    assert any("Posición cerrada" in t for t in titles)
    # Los campos del embed incluyen el PnL.
    closed = next(n for n in channel.received if "cerrada" in n.title)
    assert "PnL" in closed.fields


async def test_kill_switch_is_critical():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(KillSwitchTriggered(source="t", reason="drawdown", drawdown_pct=25.0))
    await asyncio.sleep(0.05)
    await notifier.stop()
    await notifications.stop()
    await bus.stop()

    assert channel.received
    assert channel.received[0].level.value == "critical"
    assert notifier.recent()  # quedó registrado en el buffer reciente

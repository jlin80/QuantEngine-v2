"""Notificaciones del Research Lab por Discord (canal falso, sin red)."""

import asyncio

from app.core.events.bus import EventBus
from app.notifications.models import Notification
from app.notifications.service import NotificationService
from app.research import events as ev
from app.research.notifications import ResearchNotifier


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


async def _wired() -> tuple[EventBus, NotificationService, _RecordingChannel, ResearchNotifier]:
    bus = EventBus()
    await bus.start()
    channel = _RecordingChannel()
    notifications = NotificationService()
    notifications.register_channel(channel)
    await notifications.start()
    notifier = ResearchNotifier(notifications, bus)
    await notifier.start()
    return bus, notifications, channel, notifier


async def _teardown(bus, notifications, notifier) -> None:
    await asyncio.sleep(0.05)
    await notifier.stop()
    await notifications.stop()
    await bus.stop()


async def test_research_lifecycle_events_become_embeds():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(
        ev.ExperimentCreated(source="research", experiment_id="e1", label="gen", kind="generation")
    )
    await bus.publish(
        ev.StrategyGenerated(source="research", count=24, symbol="BTCUSDT", sample_name="ema")
    )
    await bus.publish(
        ev.CandidateQualified(
            source="research",
            genome_id="g1",
            name="ema+macd",
            symbol="BTCUSDT",
            objective="sharpe",
            score=1.4,
            metrics={"profit_factor": 1.8, "sharpe": 1.4},
        )
    )
    await _teardown(bus, notifications, notifier)

    titles = [n.title for n in channel.received]
    assert any("Nuevo experimento" in t for t in titles)
    assert any("estrategias generadas" in t for t in titles)
    assert any("Nueva candidata" in t for t in titles)
    candidate = next(n for n in channel.received if "candidata" in n.title)
    assert "profit_factor" in candidate.fields


async def test_promotion_and_shadow_events_delivered():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(
        ev.StrategyPromoted(
            source="research", genome_id="g1", name="ema", operator="naz", improvement=0.4
        )
    )
    await bus.publish(
        ev.ShadowReportReady(
            source="research",
            official="ema_slow",
            challenger="ema_fast",
            better=True,
            significant=True,
            p_value=0.02,
            effect_r=0.12,
        )
    )
    await _teardown(bus, notifications, notifier)

    titles = [n.title for n in channel.received]
    assert any("promovida" in t for t in titles)
    assert any("Shadow" in t for t in titles)
    assert notifier.stats["sent"] >= 2


async def test_research_failure_is_reported_as_error():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(
        ev.ResearchFailed(source="research", operation="generation", error="dataset vacío")
    )
    await _teardown(bus, notifications, notifier)

    assert channel.received
    assert channel.received[0].level.value == "error"

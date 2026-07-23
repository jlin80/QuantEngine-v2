"""Pruebas de notificaciones: fan-out, filtro por nivel y canal Discord."""

import httpx
from app.notifications.channels.discord import DiscordWebhookChannel
from app.notifications.models import Notification, NotificationLevel
from app.notifications.service import NotificationService


class _RecordingChannel:
    def __init__(self, name: str = "recorder", fail: bool = False) -> None:
        self._name = name
        self._fail = fail
        self.sent: list[Notification] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send(self, notification: Notification) -> None:
        if self._fail:
            raise RuntimeError("channel down")
        self.sent.append(notification)

    async def close(self) -> None:
        pass


async def test_fanout_and_error_isolation():
    service = NotificationService()
    good = _RecordingChannel("good")
    bad = _RecordingChannel("bad", fail=True)
    service.register_channel(good)
    service.register_channel(bad)
    await service.start()

    await service.send("t", "m", level=NotificationLevel.ERROR)

    assert len(good.sent) == 1, "el canal sano entrega aunque otro falle"
    assert service.stats == {"delivered": 1, "failed": 1}
    await service.stop()


async def test_min_level_filter():
    service = NotificationService(min_level=NotificationLevel.WARNING)
    channel = _RecordingChannel()
    service.register_channel(channel)
    await service.start()

    await service.send("info", "ignored", level=NotificationLevel.INFO)
    await service.send("warn", "delivered", level=NotificationLevel.WARNING)

    assert [n.title for n in channel.sent] == ["warn"]
    await service.stop()


async def test_disabled_service_sends_nothing():
    service = NotificationService(enabled=False)
    channel = _RecordingChannel()
    service.register_channel(channel)
    await service.start()
    await service.send("t", "m", level=NotificationLevel.CRITICAL)
    assert channel.sent == []
    await service.stop()


async def test_discord_channel_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(204)

    channel = DiscordWebhookChannel(
        "https://discord.com/api/webhooks/123/abc",
        transport=httpx.MockTransport(handler),
    )
    await channel.send(
        Notification(
            title="Trade abierto",
            message="XAUUSD long",
            level=NotificationLevel.SUCCESS,
            fields={"Volumen": "0.01"},
            source="paper",
        )
    )
    await channel.close()

    embed = captured["payload"]["embeds"][0]
    assert embed["title"] == "Trade abierto"
    assert embed["color"] == 0x2ECC71
    assert embed["fields"][0]["name"] == "Volumen"
    assert "paper" in embed["footer"]["text"]


async def test_discord_channel_retries_on_5xx_then_fails():
    import pytest
    from app.core.exceptions import NotificationError

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    channel = DiscordWebhookChannel(
        "https://discord.com/api/webhooks/123/abc",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(NotificationError):
        await channel.send(Notification(title="t", message="m"))
    assert calls["n"] == 2
    await channel.close()

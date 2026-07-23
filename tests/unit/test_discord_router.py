"""Enrutado Discord multicanal: por canal explícito, por fuente y a errores."""

import httpx
from app.notifications.channels.discord_router import build_routed_discord
from app.notifications.models import Notification, NotificationLevel


def _router_capturing(captured: list[str]):
    """A routed channel whose every webhook records which host it hit."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    return build_routed_discord(
        "https://discord.com/api/webhooks/DEFAULT/x",
        {
            "errores": "https://discord.com/api/webhooks/ERRORES/x",
            "trading": "https://discord.com/api/webhooks/TRADING/x",
            "reportes": "https://discord.com/api/webhooks/REPORTES/x",
        },
        transport=transport,
    )


async def test_explicit_channel_wins():
    captured: list[str] = []
    router = _router_capturing(captured)
    await router.send(Notification(title="t", message="m", channel="reportes"))
    assert "REPORTES" in captured[0]
    await router.close()


async def test_routes_by_source():
    captured: list[str] = []
    router = _router_capturing(captured)
    await router.send(Notification(title="t", message="m", source="execution"))
    assert "TRADING" in captured[0]
    await router.close()


async def test_errors_go_to_errores_channel():
    captured: list[str] = []
    router = _router_capturing(captured)
    await router.send(
        Notification(title="t", message="m", source="engine", level=NotificationLevel.ERROR)
    )
    assert "ERRORES" in captured[0]
    await router.close()


async def test_unknown_source_falls_back_to_default():
    captured: list[str] = []
    router = _router_capturing(captured)
    await router.send(Notification(title="t", message="m", source="mystery"))
    assert "DEFAULT" in captured[0]
    await router.close()


async def test_single_webhook_behaves_like_before():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(204)

    router = build_routed_discord(
        "https://discord.com/api/webhooks/DEFAULT/x",
        {},
        transport=httpx.MockTransport(handler),
    )
    await router.send(Notification(title="t", message="m", source="execution", channel="trading"))
    assert "DEFAULT" in captured[0], "sin canales lógicos, todo va al webhook por defecto"
    await router.close()

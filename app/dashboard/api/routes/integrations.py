"""Discord and Notion integration admin endpoints (secrets always masked)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.dashboard.api.audit import audit_log
from app.notifications.models import NotificationLevel
from app.notifications.service import NotificationService

router = APIRouter(tags=["integrations"])


def _mask_webhook(url: str) -> str:
    """Mask a Discord webhook URL, never revealing the token.

    Args:
        url: Full webhook URL.

    Returns:
        A masked representation safe to show in the UI.
    """
    if not url:
        return ""
    marker = "/webhooks/"
    idx = url.find(marker)
    if idx == -1:
        return "configured"
    return url[: idx + len(marker)] + "****"


@router.get("/integrations/discord")
async def discord_status(request: Request) -> dict[str, Any]:
    """Discord webhook status with a masked URL (never the full secret)."""
    settings = request.app.state.settings
    webhook = str(settings.discord.webhook_url.get_secret_value())
    container: Container | None = request.app.state.container
    stats: dict[str, int] = {}
    if container is not None and container.contains(NotificationService):
        stats = container.resolve(NotificationService).stats
    return {
        "enabled": bool(settings.discord.enabled),
        "configured": bool(webhook),
        "min_level": str(settings.discord.min_level),
        "webhook_masked": _mask_webhook(webhook),
        "stats": stats,
    }


@router.post("/integrations/discord/test")
async def discord_test(request: Request) -> dict[str, Any]:
    """Send a test notification through the Discord channel (audited)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(NotificationService):
        raise HTTPException(status_code=503, detail="Notification service not available")
    service = container.resolve(NotificationService)
    await service.send(
        "Dashboard test",
        "Test notification triggered from the Quant Engine dashboard.",
        level=NotificationLevel.INFO,
        source="dashboard",
    )
    audit_log.record(action="discord.test")
    return {"sent": True, "stats": service.stats}


@router.get("/integrations/notion")
async def notion_status(request: Request) -> dict[str, Any]:
    """Notion integration status (prepared; document mirror is manual)."""
    settings = request.app.state.settings
    api_key = str(settings.notion.api_key.get_secret_value())
    return {
        "enabled": bool(settings.notion.enabled),
        "configured": bool(api_key and settings.notion.database_id),
        "has_database": bool(settings.notion.database_id),
    }

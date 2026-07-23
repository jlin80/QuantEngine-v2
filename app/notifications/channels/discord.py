"""Canal Discord vía Webhook (embeds con color por severidad).

Maneja rate-limit (HTTP 429 + ``retry_after``) y reintentos con backoff.
"""

import asyncio
import logging

import httpx

from app.core.exceptions import NotificationError
from app.notifications.models import Notification, NotificationLevel

_EMBED_COLORS: dict[NotificationLevel, int] = {
    NotificationLevel.INFO: 0x3498DB,  # azul
    NotificationLevel.SUCCESS: 0x2ECC71,  # verde
    NotificationLevel.WARNING: 0xF39C12,  # ámbar
    NotificationLevel.ERROR: 0xE74C3C,  # rojo
    NotificationLevel.CRITICAL: 0x992D22,  # rojo oscuro
}

_MAX_FIELDS = 25  # límite de la API de Discord por embed


class DiscordWebhookChannel:
    """Notification channel backed by a Discord webhook.

    Args:
        webhook_url: Full Discord webhook URL (secret — never logged).
        timeout_seconds: Per-request timeout.
        max_retries: Attempts on transient failures (429/5xx/red).
        transport: Optional httpx transport override (tests).
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not webhook_url:
            raise NotificationError("Discord webhook URL is empty", context={"channel": "discord"})
        self._webhook_url = webhook_url
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._log = logging.getLogger("app.notifications")

    @property
    def channel_name(self) -> str:
        """Channel identifier."""
        return "discord"

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily build the shared HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self._client

    def _build_payload(self, notification: Notification) -> dict[str, object]:
        """Map a notification to a Discord embed payload."""
        embed: dict[str, object] = {
            "title": notification.title[:256],
            "description": notification.message[:4000],
            "color": _EMBED_COLORS[notification.level],
            "timestamp": notification.timestamp.isoformat(),
            "footer": {"text": f"quant-engine · {notification.source}"},
        }
        if notification.fields:
            embed["fields"] = [
                {"name": str(k)[:256], "value": str(v)[:1024], "inline": True}
                for k, v in list(notification.fields.items())[:_MAX_FIELDS]
            ]
        return {"embeds": [embed]}

    async def send(self, notification: Notification) -> None:
        """Deliver a notification to the webhook.

        Args:
            notification: Message to deliver.

        Raises:
            NotificationError: When every retry is exhausted or Discord
                rejects the payload permanently.
        """
        payload = self._build_payload(notification)
        client = self._get_client()
        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.post(self._webhook_url, json=payload)
            except httpx.HTTPError as exc:
                last_error = repr(exc)
                self._log.warning(
                    "Discord delivery attempt %d/%d failed: %s",
                    attempt,
                    self._max_retries,
                    last_error,
                )
                await asyncio.sleep(min(2.0 * attempt, 10.0))
                continue

            if response.status_code in (200, 204):
                return
            if response.status_code == 429:
                retry_after = self._retry_after_seconds(response)
                self._log.warning("Discord rate limited; waiting %.2fs", retry_after)
                await asyncio.sleep(retry_after)
                continue
            if 500 <= response.status_code < 600:
                last_error = f"HTTP {response.status_code}"
                await asyncio.sleep(min(2.0 * attempt, 10.0))
                continue
            # 4xx distinto de 429: error permanente, reintentar no ayuda.
            raise NotificationError(
                "Discord rejected the notification",
                context={
                    "channel": "discord",
                    "status": response.status_code,
                    "body": response.text[:300],
                },
            )
        raise NotificationError(
            "Discord delivery failed after retries",
            context={"channel": "discord", "attempts": self._max_retries, "error": last_error},
        )

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        """Extract the wait time from a 429 response (default 2s)."""
        try:
            data = response.json()
            value = float(data.get("retry_after", 2.0))
        except (ValueError, KeyError, TypeError):
            value = 2.0
        return max(0.5, min(value, 30.0))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

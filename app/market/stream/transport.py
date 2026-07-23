"""Transporte WebSocket abstracto (inyectable para poder testear sin red)."""

import abc
import contextlib

import websockets
from websockets.asyncio.client import ClientConnection

from app.core.exceptions import ProviderError


class WSTransport(abc.ABC):
    """Minimal WebSocket transport contract used by the connection manager."""

    @abc.abstractmethod
    async def connect(self, url: str) -> None:
        """Open the connection.

        Raises:
            ProviderError: Si la conexión no puede establecerse.
        """

    @abc.abstractmethod
    async def send(self, data: str) -> None:
        """Send one text frame."""

    @abc.abstractmethod
    async def receive(self) -> str:
        """Block until the next text frame arrives.

        Raises:
            ProviderError: Si la conexión se cerró o falló.
        """

    @abc.abstractmethod
    async def ping(self) -> float:
        """Send a protocol ping and return the RTT in milliseconds."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Close the connection (never raises)."""


class WebsocketsTransport(WSTransport):
    """Transport real sobre la librería ``websockets`` (con compresión).

    Args:
        compression: Negociar ``permessage-deflate`` si el servidor lo soporta.
        open_timeout_seconds: Timeout del handshake.
    """

    def __init__(self, *, compression: bool = True, open_timeout_seconds: float = 10.0) -> None:
        self._compression = "deflate" if compression else None
        self._open_timeout = open_timeout_seconds
        self._ws: ClientConnection | None = None

    async def connect(self, url: str) -> None:
        """Open the WebSocket connection."""
        try:
            self._ws = await websockets.connect(
                url,
                compression=self._compression,
                open_timeout=self._open_timeout,
                ping_interval=None,  # el heartbeat lo gobierna WSConnection
                max_queue=4096,
            )
        except (OSError, websockets.exceptions.WebSocketException, TimeoutError) as exc:
            raise ProviderError(
                "WebSocket connect failed", context={"url": url, "error": repr(exc)}
            ) from exc

    def _require(self) -> ClientConnection:
        """Return the live connection or raise."""
        if self._ws is None:
            raise ProviderError("WebSocket not connected")
        return self._ws

    async def send(self, data: str) -> None:
        """Send one text frame."""
        try:
            await self._require().send(data)
        except websockets.exceptions.WebSocketException as exc:
            raise ProviderError("WebSocket send failed", context={"error": repr(exc)}) from exc

    async def receive(self) -> str:
        """Receive the next text frame."""
        try:
            message = await self._require().recv()
        except websockets.exceptions.WebSocketException as exc:
            raise ProviderError("WebSocket closed", context={"error": repr(exc)}) from exc
        return message if isinstance(message, str) else message.decode("utf-8", "replace")

    async def ping(self) -> float:
        """Protocol-level ping; returns RTT in milliseconds."""
        import time

        started = time.monotonic()
        try:
            pong = await self._require().ping()
            await pong
        except websockets.exceptions.WebSocketException as exc:
            raise ProviderError("WebSocket ping failed", context={"error": repr(exc)}) from exc
        return (time.monotonic() - started) * 1000.0

    async def close(self) -> None:
        """Close the connection quietly."""
        if self._ws is not None:
            with contextlib.suppress(websockets.exceptions.WebSocketException):
                await self._ws.close()
            self._ws = None

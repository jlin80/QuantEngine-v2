"""Registro central de conexiones WebSocket con métricas agregadas."""

import asyncio
from typing import Any

from app.market.models import ConnectionState
from app.market.stream.connection import WSConnection


class WebSocketManager:
    """Owns every streaming connection in the system.

    Los proveedores registran aquí sus conexiones; el manager expone estado
    agregado para el health monitor y el dashboard, y permite apagarlo todo
    de una vez.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WSConnection] = {}

    def register(self, name: str, connection: WSConnection) -> None:
        """Track a connection under a unique name.

        Args:
            name: Identificador (p. ej. ``binance:public``).
            connection: Conexión supervisada.
        """
        self._connections[name] = connection

    def get(self, name: str) -> WSConnection | None:
        """Return a connection by name (``None`` si no existe)."""
        return self._connections.get(name)

    @property
    def connection_count(self) -> int:
        """How many connections are registered."""
        return len(self._connections)

    @property
    def connected_count(self) -> int:
        """How many connections are currently established."""
        return sum(1 for conn in self._connections.values() if conn.is_connected)

    @property
    def total_reconnections(self) -> int:
        """Reconexiones acumuladas de todas las conexiones."""
        return sum(conn.reconnections for conn in self._connections.values())

    def all_healthy(self) -> bool:
        """Whether every registered connection is established."""
        return all(
            conn.state in (ConnectionState.CONNECTED, ConnectionState.STOPPED)
            for conn in self._connections.values()
        )

    async def stop_all(self) -> None:
        """Stop every connection concurrently (never raises)."""
        if self._connections:
            await asyncio.gather(*(conn.stop() for conn in self._connections.values()))

    def status(self) -> dict[str, Any]:
        """Aggregate + per-connection diagnostic snapshot."""
        return {
            "connections": self.connection_count,
            "connected": self.connected_count,
            "reconnections_total": self.total_reconnections,
            "detail": {name: conn.status() for name, conn in self._connections.items()},
        }

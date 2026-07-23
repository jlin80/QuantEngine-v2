"""WSConnection: reconexión automática, backoff, staleness y métricas."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from app.core.exceptions import ProviderError
from app.market.models import ConnectionState
from app.market.stream.connection import WSConnection, WSConnectionConfig
from app.market.stream.manager import WebSocketManager
from app.market.stream.transport import WSTransport


class FakeTransport(WSTransport):
    """Transporte guionizado: entrega mensajes y luego 'muere'."""

    def __init__(self, script: list[str], *, fail_connect: bool = False) -> None:
        self.script = list(script)
        self.fail_connect = fail_connect
        self.sent: list[str] = []
        self.closed = False

    async def connect(self, url: str) -> None:
        if self.fail_connect:
            raise ProviderError("connect refused")

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def receive(self) -> str:
        if self.script:
            return self.script.pop(0)
        raise ProviderError("connection dropped")

    async def ping(self) -> float:
        return 1.0

    async def close(self) -> None:
        self.closed = True


def _config(**overrides: float) -> WSConnectionConfig:
    defaults: dict[str, Any] = {
        "name": "test",
        "url": "wss://fake",
        "ping_interval_seconds": 0.0,
        "stale_after_seconds": 0.0,
        "backoff_base_seconds": 0.01,
        "backoff_cap_seconds": 0.02,
        "max_retries": 0,
    }
    defaults.update(overrides)
    return WSConnectionConfig(**defaults)


async def _wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout)


async def test_messages_flow_and_reconnection():
    transports: list[FakeTransport] = []
    received: list[str] = []
    connects: list[bool] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(["msg-1", "msg-2"] if not transports else ["msg-3"])
        transports.append(transport)
        return transport

    async def on_message(message: str) -> None:
        received.append(message)

    async def on_connected(was_reconnection: bool) -> None:
        connects.append(was_reconnection)

    connection = WSConnection(
        _config(), transport_factory=factory, on_message=on_message, on_connected=on_connected
    )
    await connection.start()
    try:
        await _wait_for(lambda: len(received) >= 3)
        assert received == ["msg-1", "msg-2", "msg-3"]
        # La primera conexión no es reconexión; la segunda sí.
        assert connects[0] is False
        assert True in connects[1:]
        assert connection.reconnections >= 1
        assert connection.messages_received == 3
    finally:
        await connection.stop()
    assert connection.state is ConnectionState.STOPPED


async def test_connect_failures_apply_backoff_and_recover():
    attempts: list[FakeTransport] = []

    def factory() -> FakeTransport:
        # Dos intentos fallidos, luego una conexión que entrega un mensaje.
        transport = FakeTransport(["ok"], fail_connect=len(attempts) < 2)
        attempts.append(transport)
        return transport

    received: list[str] = []

    async def on_message(message: str) -> None:
        received.append(message)

    connection = WSConnection(_config(), transport_factory=factory, on_message=on_message)
    await connection.start()
    try:
        await _wait_for(lambda: len(received) == 1)
        assert len(attempts) >= 3
    finally:
        await connection.stop()


async def test_max_retries_gives_up():
    def factory() -> FakeTransport:
        return FakeTransport([], fail_connect=True)

    async def on_message(message: str) -> None:  # pragma: no cover
        pass

    connection = WSConnection(
        _config(max_retries=2), transport_factory=factory, on_message=on_message
    )
    await connection.start()
    await _wait_for(lambda: connection.state is ConnectionState.DISCONNECTED)
    await connection.stop()


async def test_stale_connection_forces_reconnect():
    class SilentTransport(FakeTransport):
        async def receive(self) -> str:
            await asyncio.sleep(60)  # jamás responde
            raise AssertionError("unreachable")

    transports: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = SilentTransport([]) if not transports else FakeTransport(["alive"])
        transports.append(transport)
        return transport

    received: list[str] = []

    async def on_message(message: str) -> None:
        received.append(message)

    connection = WSConnection(
        _config(stale_after_seconds=0.05), transport_factory=factory, on_message=on_message
    )
    await connection.start()
    try:
        await _wait_for(lambda: received == ["alive"])
        assert len(transports) == 2  # la muda fue reciclada
    finally:
        await connection.stop()


async def test_handler_errors_do_not_kill_connection():
    def factory() -> FakeTransport:
        return FakeTransport(["a", "b", "c"])

    seen: list[str] = []

    async def on_message(message: str) -> None:
        seen.append(message)
        if message == "a":
            raise RuntimeError("handler bug")

    connection = WSConnection(_config(), transport_factory=factory, on_message=on_message)
    await connection.start()
    try:
        await _wait_for(lambda: len(seen) >= 3)
    finally:
        await connection.stop()


async def test_send_requires_connection():
    connection = WSConnection(
        _config(), transport_factory=lambda: FakeTransport([]), on_message=_noop
    )
    with pytest.raises(ProviderError):
        await connection.send("hello")


async def _noop(message: str) -> None:  # pragma: no cover
    pass


async def test_manager_aggregates_status():
    manager = WebSocketManager()
    transport = FakeTransport(["x"])
    connection = WSConnection(_config(), transport_factory=lambda: transport, on_message=_noop)
    manager.register("test:public", connection)
    assert manager.connection_count == 1
    await connection.start()
    try:
        await _wait_for(lambda: connection.messages_received >= 1)
        status = manager.status()
        assert "test:public" in status["detail"]
    finally:
        await manager.stop_all()
    assert connection.state is ConnectionState.STOPPED

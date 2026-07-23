"""BaseWSProvider: suscripción, resuscripción tras reconexión y pipeline."""

import asyncio
import json
from collections.abc import Callable

from app.config.settings import MarketProviderSettings, MarketWSSettings
from app.core.exceptions import ProviderError
from app.market.models import ChannelType, Trade
from app.market.providers.binance import BinanceProvider
from app.market.providers.prepared import MT5Provider
from app.market.stream.transport import WSTransport


class ScriptedTransport(WSTransport):
    """Entrega mensajes guionizados y muere; registra lo enviado."""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.sent: list[str] = []

    async def connect(self, url: str) -> None:
        pass

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def receive(self) -> str:
        if self.script:
            item = self.script.pop(0)
            if item == "<hang>":
                await asyncio.sleep(60)
            return item
        raise ProviderError("dropped")

    async def ping(self) -> float:
        return 1.0

    async def close(self) -> None:
        pass


def _ws_settings() -> MarketWSSettings:
    return MarketWSSettings(
        ping_interval_seconds=0.0,
        stale_after_seconds=0.0,
        backoff_base_seconds=0.01,
        backoff_cap_seconds=0.02,
    )


_TRADE_FRAME = json.dumps(
    {"e": "trade", "s": "BTCUSDT", "t": 7, "p": "65000", "q": "0.5", "T": 1752580800000, "m": False}
)


async def _wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout)


async def test_provider_normalizes_into_sink_and_resubscribes():
    transports: list[ScriptedTransport] = []

    def factory() -> ScriptedTransport:
        # Primera conexión: entrega un trade y muere. Segunda: queda colgada.
        transport = ScriptedTransport([_TRADE_FRAME] if not transports else ["<hang>"])
        transports.append(transport)
        return transport

    received: list[object] = []
    provider = BinanceProvider(MarketProviderSettings(), _ws_settings(), transport_factory=factory)
    provider.set_sink(received.append)
    await provider.start()
    try:
        await provider.subscribe("BTCUSDT", [ChannelType.TRADES, ChannelType.TICKER])
        await _wait_for(lambda: len(received) >= 1)
        trade = received[0]
        assert isinstance(trade, Trade)
        assert trade.symbol == "BTCUSDT"

        # Tras la caída, la segunda conexión debe recibir la resuscripción.
        await _wait_for(lambda: len(transports) >= 2 and len(transports[1].sent) >= 1)
        resubscribe = json.loads(transports[1].sent[0])
        assert resubscribe["method"] == "SUBSCRIBE"
        assert "btcusdt@trade" in resubscribe["params"]
        assert "btcusdt@bookTicker" in resubscribe["params"]
    finally:
        await provider.stop()


async def test_provider_skips_unsupported_channels():
    def factory() -> ScriptedTransport:
        return ScriptedTransport(["<hang>"])

    provider = BinanceProvider(MarketProviderSettings(), _ws_settings(), transport_factory=factory)
    await provider.start()
    try:
        await provider.subscribe("BTCUSDT", [ChannelType.OPEN_INTEREST])  # no soportado en WS
        assert provider.subscriptions == {}
    finally:
        await provider.stop()


async def test_provider_ignores_malformed_frames():
    def factory() -> ScriptedTransport:
        return ScriptedTransport(["not-json", _TRADE_FRAME, "<hang>"])

    received: list[object] = []
    provider = BinanceProvider(MarketProviderSettings(), _ws_settings(), transport_factory=factory)
    provider.set_sink(received.append)
    await provider.start()
    try:
        await _wait_for(lambda: len(received) == 1)
        assert provider.status()["parse_errors"] == 1
    finally:
        await provider.stop()


async def test_prepared_provider_fails_clearly():
    provider = MT5Provider(MarketProviderSettings())
    try:
        await provider.start()
    except ProviderError as exc:
        assert "prepared" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("PreparedProvider.start should raise")
    assert provider.status()["state"] == "prepared"

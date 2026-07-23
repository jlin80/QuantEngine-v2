"""Pruebas del proveedor de datos MetaTrader 5 (polling), con MT5 falso.

Sin terminal real: se inyecta un módulo ``MetaTrader5`` doble que sirve ticks y
velas deterministas. Se valida normalización de Ticker, mapeo de velas,
suscripción y el ciclo start→emit→stop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from app.brokers.mt5.connection import MT5Connection, MT5ConnectionConfig
from app.market.models import ChannelType, ConnectionState, Ticker, Timeframe
from app.market.providers.mt5 import MT5MarketProvider


class FakeMT5:
    """Doble de ``MetaTrader5`` para el feed."""

    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5

    def __init__(self, *, bid: float = 2000.0, ask: float = 2000.4) -> None:
        self._bid = bid
        self._ask = ask
        self.selected: list[str] = []

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def account_info(self) -> Any:
        return SimpleNamespace(login=1, balance=1000.0)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.selected.append(symbol)
        return True

    def symbol_info_tick(self, symbol: str) -> Any:
        return SimpleNamespace(
            bid=self._bid, ask=self._ask, last=self._ask, time_msc=1_700_000_000_000
        )

    def copy_rates_from_pos(
        self, symbol: str, tf: int, start: int, count: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "time": 1_700_000_000,
                "open": 1999.0,
                "high": 2001.0,
                "low": 1998.5,
                "close": 2000.0,
                "tick_volume": 42,
            }
        ]


def _provider(fake: FakeMT5) -> MT5MarketProvider:
    conn = MT5Connection(MT5ConnectionConfig(login=1, password="x", server="s"), mt5=fake)
    return MT5MarketProvider(conn, poll_seconds=0.05)


def test_capabilities_and_initial_state() -> None:
    provider = _provider(FakeMT5())
    assert ChannelType.TICKER in provider.capabilities
    assert provider.connection_state is ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_subscribe_preserves_symbol_case() -> None:
    # Los símbolos MT5 de Exness llevan sufijos en minúscula (XAUUSDm): el
    # provider NUNCA debe normalizar a mayúsculas o el tick vendría vacío.
    fake = FakeMT5()
    provider = _provider(fake)
    await provider.subscribe("XAUUSDm", [ChannelType.TICKER])
    assert "XAUUSDm" in fake.selected
    assert provider.status()["symbols"] == ["XAUUSDm"]


def test_poll_once_builds_ticker() -> None:
    fake = FakeMT5(bid=2000.0, ask=2000.4)
    provider = _provider(fake)
    provider._conn.connect()

    ticks = provider._poll_once(("XAUUSD",))

    assert len(ticks) == 1
    ticker = ticks[0]
    assert isinstance(ticker, Ticker)
    assert ticker.bid == pytest.approx(2000.0)
    assert ticker.ask == pytest.approx(2000.4)
    assert ticker.provider == "mt5"


@pytest.mark.asyncio
async def test_fetch_candles_maps_rows() -> None:
    provider = _provider(FakeMT5())
    provider._conn.connect()

    candles = await provider.fetch_candles("XAUUSD", Timeframe.M1, limit=1)

    assert len(candles) == 1
    candle = candles[0]
    assert candle.open == pytest.approx(1999.0)
    assert candle.close == pytest.approx(2000.0)
    assert candle.volume == pytest.approx(42.0)
    assert candle.closed is True


@pytest.mark.asyncio
async def test_start_emits_and_stop() -> None:
    fake = FakeMT5()
    provider = _provider(fake)
    received: list[Ticker] = []
    provider.set_sink(received.append)

    await provider.subscribe("XAUUSD", [ChannelType.TICKER])
    await provider.start()
    assert provider.connection_state is ConnectionState.CONNECTED
    await asyncio.sleep(0.15)
    await provider.stop()

    assert received, "el feed debió emitir al menos un ticker"
    assert provider.connection_state is ConnectionState.STOPPED

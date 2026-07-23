"""Proveedores preparados (estructura lista, implementación en fase futura).

Bitget, OANDA, MetaTrader 5 e Interactive Brokers declaran sus capacidades y
cumplen la interfaz, pero ``start()`` falla con un error claro hasta que se
implementen. Añadir un proveedor nuevo NUNCA toca el núcleo: se implementa la
interfaz y se registra en el :class:`~app.market.providers.registry.ProviderRegistry`.
"""

from collections.abc import Sequence
from typing import Any

from app.config.settings import MarketProviderSettings
from app.core.exceptions import ProviderError
from app.market.interfaces.provider import MarketDataProvider
from app.market.models import (
    Candle,
    ChannelType,
    ConnectionState,
    OrderBookDelta,
    Timeframe,
)


class PreparedProvider(MarketDataProvider):
    """Base de los proveedores aún no implementados.

    Args:
        name: Identificador del proveedor.
        capabilities: Canales que servirá cuando se implemente.
        note: Nota para logs/diagnóstico sobre qué falta.
    """

    def __init__(self, name: str, capabilities: frozenset[ChannelType], note: str) -> None:
        super().__init__(name)
        self._capabilities = capabilities
        self._note = note

    @property
    def capabilities(self) -> frozenset[ChannelType]:
        """Channels que servirá cuando se implemente."""
        return self._capabilities

    def _not_ready(self) -> ProviderError:
        """Uniform 'not implemented yet' error."""
        return ProviderError(
            f"Provider '{self._name}' is prepared but not implemented yet",
            context={"provider": self._name, "note": self._note},
            recoverable=False,
        )

    async def start(self) -> None:
        """Not implemented yet."""
        raise self._not_ready()

    async def stop(self) -> None:
        """Nothing to stop."""

    async def subscribe(self, symbol: str, channels: Sequence[ChannelType]) -> None:
        """Not implemented yet."""
        raise self._not_ready()

    async def unsubscribe(self, symbol: str) -> None:
        """Nothing to unsubscribe."""

    @property
    def connection_state(self) -> ConnectionState:
        """Always disconnected until implemented."""
        return ConnectionState.DISCONNECTED

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot."""
        return {"provider": self._name, "state": "prepared", "note": self._note}

    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """Not implemented yet."""
        raise self._not_ready()

    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Not implemented yet."""
        raise self._not_ready()

    async def fetch_server_time(self) -> float:
        """Not implemented yet."""
        raise self._not_ready()


class BitgetProvider(PreparedProvider):
    """Bitget — WebSocket público v2 (preparado)."""

    def __init__(self, provider_settings: MarketProviderSettings, **_: object) -> None:
        super().__init__(
            "bitget",
            frozenset(
                {
                    ChannelType.TICKER,
                    ChannelType.TRADES,
                    ChannelType.ORDERBOOK,
                    ChannelType.CANDLES,
                }
            ),
            "WS público wss://ws.bitget.com/v2/ws/public; misma familia que OKX.",
        )


class OandaProvider(PreparedProvider):
    """OANDA — streaming REST v20 para forex/XAUUSD (preparado).

    OANDA no usa WebSocket: su API v20 sirve un stream HTTP chunked
    (``/v3/accounts/{id}/pricing/stream``). Requiere cuenta y token.
    """

    def __init__(self, provider_settings: MarketProviderSettings, **_: object) -> None:
        super().__init__(
            "oanda",
            frozenset({ChannelType.TICKER}),
            "Streaming HTTP v20 con token; símbolo XAU_USD ↔ XAUUSD.",
        )


class MT5Provider(PreparedProvider):
    """MetaTrader 5 — polling local vía paquete ``MetaTrader5`` (preparado).

    MT5 no ofrece WebSocket: el proveedor hará polling de ticks con
    ``copy_ticks_from`` sobre el terminal local (solo Windows).
    """

    def __init__(self, provider_settings: MarketProviderSettings, **_: object) -> None:
        super().__init__(
            "mt5",
            frozenset({ChannelType.TICKER, ChannelType.TRADES, ChannelType.CANDLES}),
            "Requiere terminal MT5 local y paquete MetaTrader5 (Windows).",
        )


class IBKRProvider(PreparedProvider):
    """Interactive Brokers — TWS/Gateway API (estructura preparada)."""

    def __init__(self, provider_settings: MarketProviderSettings, **_: object) -> None:
        super().__init__(
            "ibkr",
            frozenset(
                {
                    ChannelType.TICKER,
                    ChannelType.TRADES,
                    ChannelType.ORDERBOOK,
                    ChannelType.CANDLES,
                }
            ),
            "Vía TWS/IB Gateway (ib_insync o API nativa); requiere sesión local.",
        )

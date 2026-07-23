"""Proveedor Bybit (API pública v5, categoría spot por defecto)."""

import json
from collections.abc import Sequence

from app.config.settings import MarketProviderSettings, MarketWSSettings
from app.market.models import Candle, ChannelType, DepthLevel, OrderBookDelta, Timeframe
from app.market.normalizer.base import as_float, local_now, ts_from_ms
from app.market.normalizer.bybit import BybitNormalizer
from app.market.providers.base import BaseWSProvider

_CHANNEL_TOPICS: dict[ChannelType, str] = {
    ChannelType.TICKER: "tickers.{s}",
    ChannelType.TRADES: "publicTrade.{s}",
    ChannelType.ORDERBOOK: "orderbook.50.{s}",
    ChannelType.CANDLES: "kline.1.{s}",
    ChannelType.LIQUIDATIONS: "liquidation.{s}",
}

_KLINE_INTERVALS: dict[Timeframe, str] = {
    Timeframe.M1: "1",
    Timeframe.M3: "3",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
    Timeframe.MN1: "M",
}


class BybitProvider(BaseWSProvider):
    """Datos públicos de Bybit v5 vía WebSocket + REST.

    Args:
        provider_settings: URLs/credenciales (overrides opcionales).
        ws_settings: Parámetros de conexión.
    """

    def __init__(
        self,
        provider_settings: MarketProviderSettings,
        ws_settings: MarketWSSettings,
        **kwargs: object,
    ) -> None:
        super().__init__(
            "bybit",
            provider_settings,
            ws_settings,
            BybitNormalizer(),
            **kwargs,  # type: ignore[arg-type]
        )

    @property
    def capabilities(self) -> frozenset[ChannelType]:
        """Channels servidos por Bybit."""
        return frozenset(_CHANNEL_TOPICS)

    def _stream_url(self) -> str:
        return "wss://stream.bybit.com/v5/public/spot"

    def _rest_url(self) -> str:
        return "https://api.bybit.com"

    def _app_ping_payload(self) -> str:
        """Bybit exige ping a nivel aplicación."""
        return json.dumps({"op": "ping"})

    def _subscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        topics = [
            _CHANNEL_TOPICS[channel].format(s=symbol.upper())
            for channel in channels
            if channel in _CHANNEL_TOPICS
        ]
        return [json.dumps({"op": "subscribe", "args": topics})] if topics else []

    def _unsubscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        topics = [
            _CHANNEL_TOPICS[channel].format(s=symbol.upper())
            for channel in channels
            if channel in _CHANNEL_TOPICS
        ]
        return [json.dumps({"op": "unsubscribe", "args": topics})] if topics else []

    # ------------------------------------------------------------------
    # REST — reconstrucción de estado
    # ------------------------------------------------------------------

    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """``GET /v5/market/orderbook`` → snapshot para el reconstructor."""
        data = await self._rest_get(
            "/v5/market/orderbook",
            params={"category": "spot", "symbol": symbol.upper(), "limit": depth},
        )
        result = data.get("result", {})
        local = local_now()
        ts = result.get("ts")
        return OrderBookDelta(
            symbol=symbol.upper(),
            provider=self.name,
            bids=_rest_levels(result.get("b", ())),
            asks=_rest_levels(result.get("a", ())),
            is_snapshot=True,
            last_sequence=int(result.get("u", 0)),
            exchange_ts=ts_from_ms(ts) if ts else local,
            local_ts=local,
        )

    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """``GET /v5/market/kline`` → velas ascendentes."""
        interval = _KLINE_INTERVALS.get(timeframe)
        if interval is None:
            return []
        data = await self._rest_get(
            "/v5/market/kline",
            params={
                "category": "spot",
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit,
            },
        )
        rows = data.get("result", {}).get("list", [])
        candles: list[Candle] = []
        for row in reversed(rows):  # Bybit devuelve descendente
            start = ts_from_ms(row[0])
            candles.append(
                Candle(
                    symbol=symbol.upper(),
                    provider=self.name,
                    timeframe=timeframe,
                    start=start,
                    end=timeframe.bucket_end(start),
                    open=as_float(row[1], "open"),
                    high=as_float(row[2], "high"),
                    low=as_float(row[3], "low"),
                    close=as_float(row[4], "close"),
                    volume=as_float(row[5], "volume"),
                    closed=True,
                    source="provider",
                )
            )
        return candles

    async def fetch_server_time(self) -> float:
        """``GET /v5/market/time`` → hora del servidor en segundos Unix."""
        data = await self._rest_get("/v5/market/time")
        return float(data.get("result", {}).get("timeSecond", 0))


def _rest_levels(raw: object) -> tuple[DepthLevel, ...]:
    """Parse REST ``[[price, size], ...]`` arrays."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(
        DepthLevel(price=as_float(pair[0], "price"), size=as_float(pair[1], "size"))
        for pair in raw
        if isinstance(pair, list | tuple) and len(pair) >= 2
    )

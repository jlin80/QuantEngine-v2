"""Proveedor Binance (streams públicos spot; futuros vía override de URLs)."""

import json
from collections.abc import Sequence

from app.config.settings import MarketProviderSettings, MarketWSSettings
from app.market.models import (
    Candle,
    ChannelType,
    DepthLevel,
    OrderBookDelta,
    Timeframe,
)
from app.market.normalizer.base import as_float, local_now, ts_from_ms
from app.market.normalizer.binance import BinanceNormalizer
from app.market.providers.base import BaseWSProvider

_CHANNEL_STREAMS: dict[ChannelType, str] = {
    ChannelType.TICKER: "{s}@bookTicker",
    ChannelType.TRADES: "{s}@trade",
    ChannelType.ORDERBOOK: "{s}@depth@100ms",
    ChannelType.CANDLES: "{s}@kline_1m",
    ChannelType.FUNDING: "{s}@markPrice",
    ChannelType.MARK_PRICE: "{s}@markPrice",
    ChannelType.LIQUIDATIONS: "{s}@forceOrder",
}

_KLINE_INTERVALS: dict[Timeframe, str] = {
    Timeframe.S1: "1s",
    Timeframe.M1: "1m",
    Timeframe.M3: "3m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
    Timeframe.MN1: "1M",
}


class BinanceProvider(BaseWSProvider):
    """Datos públicos de Binance vía WebSocket + REST.

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
            "binance",
            provider_settings,
            ws_settings,
            BinanceNormalizer(),
            **kwargs,  # type: ignore[arg-type]
        )
        self._request_id = 0

    @property
    def capabilities(self) -> frozenset[ChannelType]:
        """Channels servidos por Binance."""
        return frozenset(_CHANNEL_STREAMS)

    def _stream_url(self) -> str:
        return "wss://stream.binance.com:9443/ws"

    def _rest_url(self) -> str:
        return "https://api.binance.com"

    def _subscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        streams = [
            _CHANNEL_STREAMS[channel].format(s=symbol.lower())
            for channel in channels
            if channel in _CHANNEL_STREAMS
        ]
        if not streams:
            return []
        self._request_id += 1
        return [json.dumps({"method": "SUBSCRIBE", "params": streams, "id": self._request_id})]

    def _unsubscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        streams = [
            _CHANNEL_STREAMS[channel].format(s=symbol.lower())
            for channel in channels
            if channel in _CHANNEL_STREAMS
        ]
        if not streams:
            return []
        self._request_id += 1
        return [json.dumps({"method": "UNSUBSCRIBE", "params": streams, "id": self._request_id})]

    # ------------------------------------------------------------------
    # REST — reconstrucción de estado
    # ------------------------------------------------------------------

    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """``GET /api/v3/depth`` → snapshot para el reconstructor."""
        data = await self._rest_get(
            "/api/v3/depth", params={"symbol": symbol.upper(), "limit": depth}
        )
        local = local_now()
        return OrderBookDelta(
            symbol=symbol.upper(),
            provider=self.name,
            bids=_rest_levels(data.get("bids", ())),
            asks=_rest_levels(data.get("asks", ())),
            is_snapshot=True,
            last_sequence=int(data.get("lastUpdateId", 0)),
            exchange_ts=local,  # el snapshot REST no trae timestamp propio
            local_ts=local,
        )

    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """``GET /api/v3/klines`` → velas cerradas ascendentes."""
        interval = _KLINE_INTERVALS.get(timeframe)
        if interval is None:
            return []
        rows = await self._rest_get(
            "/api/v3/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        local = local_now()
        candles: list[Candle] = []
        for row in rows:
            start = ts_from_ms(row[0])
            volume = as_float(row[5], "volume")
            quote_volume = as_float(row[7], "quote_volume")
            buy_volume = as_float(row[9], "taker_buy_volume")
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
                    volume=volume,
                    buy_volume=buy_volume,
                    sell_volume=max(volume - buy_volume, 0.0),
                    vwap=quote_volume / volume if volume > 0 else 0.0,
                    trades=int(row[8]),
                    # La última fila de klines es la vela en curso.
                    closed=timeframe.bucket_end(start) <= local,
                    source="provider",
                )
            )
        return candles

    async def fetch_server_time(self) -> float:
        """``GET /api/v3/time`` → hora del servidor en segundos Unix."""
        data = await self._rest_get("/api/v3/time")
        return float(data.get("serverTime", 0)) / 1000.0


def _rest_levels(raw: object) -> tuple[DepthLevel, ...]:
    """Parse REST ``[[price, size], ...]`` arrays."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(
        DepthLevel(price=as_float(pair[0], "price"), size=as_float(pair[1], "size"))
        for pair in raw
        if isinstance(pair, list | tuple) and len(pair) >= 2
    )

"""Proveedor OKX (API pública v5)."""

import json
from collections.abc import Sequence
from typing import Any

from app.config.settings import MarketProviderSettings, MarketWSSettings
from app.market.models import Candle, ChannelType, DepthLevel, OrderBookDelta, Timeframe
from app.market.normalizer.base import as_float, local_now, ts_from_ms
from app.market.normalizer.okx import OKXNormalizer, to_okx_inst_id
from app.market.providers.base import BaseWSProvider

_CHANNELS: dict[ChannelType, str] = {
    ChannelType.TICKER: "tickers",
    ChannelType.TRADES: "trades",
    ChannelType.ORDERBOOK: "books",
    ChannelType.CANDLES: "candle1m",
    ChannelType.FUNDING: "funding-rate",
    ChannelType.OPEN_INTEREST: "open-interest",
    ChannelType.LIQUIDATIONS: "liquidation-orders",
}

_BAR_INTERVALS: dict[Timeframe, str] = {
    Timeframe.S1: "1s",
    Timeframe.M1: "1m",
    Timeframe.M3: "3m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1H",
    Timeframe.H4: "4H",
    Timeframe.D1: "1D",
    Timeframe.W1: "1W",
    Timeframe.MN1: "1M",
}


class OKXProvider(BaseWSProvider):
    """Datos públicos de OKX v5 vía WebSocket + REST.

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
            "okx",
            provider_settings,
            ws_settings,
            OKXNormalizer(),
            **kwargs,  # type: ignore[arg-type]
        )

    @property
    def capabilities(self) -> frozenset[ChannelType]:
        """Channels servidos por OKX."""
        return frozenset(_CHANNELS)

    def _stream_url(self) -> str:
        return "wss://ws.okx.com:8443/ws/v5/public"

    def _rest_url(self) -> str:
        return "https://www.okx.com"

    def _app_ping_payload(self) -> str:
        """OKX acepta el literal ``ping``."""
        return "ping"

    def _args(self, symbol: str, channels: Sequence[ChannelType]) -> list[dict[str, str]]:
        """Build OKX subscription args for a symbol."""
        inst_id = to_okx_inst_id(symbol)
        return [
            {"channel": _CHANNELS[channel], "instId": inst_id}
            for channel in channels
            if channel in _CHANNELS
        ]

    def _subscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        args = self._args(symbol, channels)
        return [json.dumps({"op": "subscribe", "args": args})] if args else []

    def _unsubscribe_payloads(self, symbol: str, channels: Sequence[ChannelType]) -> list[str]:
        args = self._args(symbol, channels)
        return [json.dumps({"op": "unsubscribe", "args": args})] if args else []

    # ------------------------------------------------------------------
    # REST — reconstrucción de estado
    # ------------------------------------------------------------------

    async def fetch_orderbook_snapshot(self, symbol: str, depth: int = 50) -> OrderBookDelta:
        """``GET /api/v5/market/books`` → snapshot para el reconstructor."""
        data = await self._rest_get(
            "/api/v5/market/books",
            params={"instId": to_okx_inst_id(symbol), "sz": depth},
        )
        rows: list[dict[str, Any]] = data.get("data", [])
        row = rows[0] if rows else {}
        local = local_now()
        ts = row.get("ts")
        return OrderBookDelta(
            symbol=symbol.upper(),
            provider=self.name,
            bids=_rest_levels(row.get("bids", ())),
            asks=_rest_levels(row.get("asks", ())),
            is_snapshot=True,
            exchange_ts=ts_from_ms(ts) if ts else local,
            local_ts=local,
        )

    async def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """``GET /api/v5/market/candles`` → velas ascendentes."""
        bar = _BAR_INTERVALS.get(timeframe)
        if bar is None:
            return []
        data = await self._rest_get(
            "/api/v5/market/candles",
            params={"instId": to_okx_inst_id(symbol), "bar": bar, "limit": limit},
        )
        candles: list[Candle] = []
        for row in reversed(data.get("data", [])):  # OKX devuelve descendente
            start = ts_from_ms(row[0])
            volume = as_float(row[5], "vol")
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
                    closed=len(row) > 8 and str(row[8]) == "1",
                    source="provider",
                )
            )
        return candles

    async def fetch_server_time(self) -> float:
        """``GET /api/v5/public/time`` → hora del servidor en segundos Unix."""
        data = await self._rest_get("/api/v5/public/time")
        rows = data.get("data", [])
        return float(rows[0].get("ts", 0)) / 1000.0 if rows else 0.0


def _rest_levels(raw: object) -> tuple[DepthLevel, ...]:
    """Parse REST level arrays (``[price, size, ...]``)."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(
        DepthLevel(price=as_float(pair[0], "price"), size=as_float(pair[1], "size"))
        for pair in raw
        if isinstance(pair, list | tuple) and len(pair) >= 2
    )

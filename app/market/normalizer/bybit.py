"""Normalizador de Bybit (API pública v5, categorías spot/linear).

Topics soportados: ``publicTrade.*``, ``tickers.*``, ``orderbook.*``,
``kline.*``, ``liquidation.*``.
"""

from typing import Any

from app.market.interfaces.normalizer import Normalizer
from app.market.models import (
    Candle,
    DepthLevel,
    Liquidation,
    MarketObject,
    OrderBookDelta,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.normalizer.base import as_float, local_now, ts_from_ms

_PROVIDER = "bybit"

_KLINE_INTERVALS: dict[str, Timeframe] = {
    "1": Timeframe.M1,
    "3": Timeframe.M3,
    "5": Timeframe.M5,
    "15": Timeframe.M15,
    "30": Timeframe.M30,
    "60": Timeframe.H1,
    "240": Timeframe.H4,
    "D": Timeframe.D1,
    "W": Timeframe.W1,
    "M": Timeframe.MN1,
}


class BybitNormalizer(Normalizer):
    """Traductor de mensajes públicos de Bybit v5 al modelo interno."""

    def normalize(self, message: dict[str, Any]) -> list[MarketObject]:
        """Translate one Bybit v5 message into internal objects."""
        topic = message.get("topic", "")
        if not topic:
            return []  # acks de suscripción, pong, etc.
        if topic.startswith("publicTrade."):
            return [self._trade(item) for item in message.get("data", ())]
        if topic.startswith("tickers."):
            ticker = self._ticker(message)
            return [ticker] if ticker is not None else []
        if topic.startswith("orderbook."):
            return [self._book(message)]
        if topic.startswith("kline."):
            symbol = topic.rsplit(".", 1)[-1]
            candles = [self._kline(symbol, item) for item in message.get("data", ())]
            return [candle for candle in candles if candle is not None]
        if topic.startswith("liquidation."):
            data = message.get("data")
            items = data if isinstance(data, list) else [data]
            return [self._liquidation(item) for item in items if isinstance(item, dict)]
        return []

    def _trade(self, item: dict[str, Any]) -> Trade:
        """``publicTrade`` item → :class:`Trade`."""
        side = TradeSide.BUY if str(item.get("S", "")).lower() == "buy" else TradeSide.SELL
        return Trade(
            symbol=str(item.get("s", "")).upper(),
            provider=_PROVIDER,
            trade_id=str(item.get("i", "")),
            price=as_float(item.get("p"), "p"),
            size=as_float(item.get("v"), "v"),
            side=side,
            exchange_ts=ts_from_ms(item.get("T") or 0),
            local_ts=local_now(),
        )

    def _ticker(self, message: dict[str, Any]) -> Ticker | None:
        """``tickers`` → :class:`Ticker` (``None`` si el delta no trae BBO)."""
        data = message.get("data") or {}
        bid, ask = data.get("bid1Price"), data.get("ask1Price")
        if bid is None or ask is None:
            return None  # los deltas de tickers pueden omitir el BBO
        last = data.get("lastPrice")
        mark = data.get("markPrice")
        index = data.get("indexPrice")
        return Ticker(
            symbol=str(data.get("symbol", "")).upper(),
            provider=_PROVIDER,
            bid=as_float(bid, "bid1Price"),
            ask=as_float(ask, "ask1Price"),
            bid_size=as_float(data.get("bid1Size", 0), "bid1Size"),
            ask_size=as_float(data.get("ask1Size", 0), "ask1Size"),
            last=as_float(last, "lastPrice") if last is not None else None,
            mark_price=as_float(mark, "markPrice") if mark is not None else None,
            index_price=as_float(index, "indexPrice") if index is not None else None,
            exchange_ts=ts_from_ms(message.get("ts") or 0),
            local_ts=local_now(),
        )

    def _book(self, message: dict[str, Any]) -> OrderBookDelta:
        """``orderbook`` → :class:`OrderBookDelta` (snapshot o delta)."""
        data = message.get("data") or {}
        sequence = int(data.get("u", 0))
        return OrderBookDelta(
            symbol=str(data.get("s", "")).upper(),
            provider=_PROVIDER,
            bids=_levels(data.get("b", ())),
            asks=_levels(data.get("a", ())),
            is_snapshot=message.get("type") == "snapshot",
            first_sequence=sequence,
            last_sequence=sequence,
            exchange_ts=ts_from_ms(message.get("ts") or 0),
            local_ts=local_now(),
        )

    def _kline(self, symbol: str, item: dict[str, Any]) -> Candle | None:
        """``kline`` item → :class:`Candle`."""
        timeframe = _KLINE_INTERVALS.get(str(item.get("interval", "")))
        if timeframe is None:
            return None
        return Candle(
            symbol=symbol.upper(),
            provider=_PROVIDER,
            timeframe=timeframe,
            start=ts_from_ms(item.get("start") or 0),
            end=ts_from_ms(as_float(item.get("end", 0), "end") + 1),
            open=as_float(item.get("open"), "open"),
            high=as_float(item.get("high"), "high"),
            low=as_float(item.get("low"), "low"),
            close=as_float(item.get("close"), "close"),
            volume=as_float(item.get("volume", 0), "volume"),
            vwap=0.0,
            trades=0,
            closed=bool(item.get("confirm", False)),
            source="provider",
        )

    def _liquidation(self, item: dict[str, Any]) -> Liquidation:
        """``liquidation`` item → :class:`Liquidation`."""
        side = TradeSide.BUY if str(item.get("side", "")).lower() == "buy" else TradeSide.SELL
        return Liquidation(
            symbol=str(item.get("symbol", "")).upper(),
            provider=_PROVIDER,
            side=side,
            price=as_float(item.get("price", 0), "price"),
            size=as_float(item.get("size", 0), "size"),
            exchange_ts=ts_from_ms(item.get("updatedTime") or 0),
            local_ts=local_now(),
        )


def _levels(raw: object) -> tuple[DepthLevel, ...]:
    """Parse ``[[price, size], ...]`` arrays into DepthLevel tuples."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(
        DepthLevel(price=as_float(pair[0], "price"), size=as_float(pair[1], "size"))
        for pair in raw
        if isinstance(pair, list | tuple) and len(pair) >= 2
    )

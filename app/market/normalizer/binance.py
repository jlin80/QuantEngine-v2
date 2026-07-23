"""Normalizador de Binance (streams públicos spot/futuros USDⓈ-M).

Formatos soportados (WebSocket):
    * ``trade`` / ``aggTrade`` — trades.
    * ``bookTicker`` — best bid/ask.
    * ``depthUpdate`` — deltas del libro (más snapshot REST).
    * ``kline`` — velas del exchange.
    * ``markPriceUpdate`` — mark price + funding (futuros).
    * ``forceOrder`` — liquidaciones (futuros).

Acepta tanto el formato de stream combinado (``{"stream": ..., "data": ...}``)
como el evento crudo.
"""

from typing import Any

from app.market.interfaces.normalizer import Normalizer
from app.market.models import (
    Candle,
    DepthLevel,
    FundingRate,
    Liquidation,
    MarketObject,
    OrderBookDelta,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.normalizer.base import as_float, local_now, ts_from_ms

_PROVIDER = "binance"


class BinanceNormalizer(Normalizer):
    """Traductor de mensajes públicos de Binance al modelo interno."""

    def normalize(self, message: dict[str, Any]) -> list[MarketObject]:
        """Translate one Binance message into internal objects."""
        data = message.get("data") if "stream" in message else message
        if not isinstance(data, dict):
            return []
        event = data.get("e")
        if event in ("trade", "aggTrade"):
            return [self._trade(data)]
        if event == "depthUpdate":
            return [self._depth(data)]
        if event == "kline":
            candle = self._kline(data)
            return [candle] if candle is not None else []
        if event == "markPriceUpdate":
            return self._mark_price(data)
        if event == "forceOrder":
            return [self._liquidation(data)]
        if event is None and "b" in data and "a" in data and "s" in data:
            return [self._book_ticker(data)]
        return []  # mensajes de control/suscripción

    @staticmethod
    def _symbol(data: dict[str, Any]) -> str:
        """Internal symbol (Binance ya usa BTCUSDT)."""
        return str(data.get("s", "")).upper()

    def _trade(self, data: dict[str, Any]) -> Trade:
        """``trade``/``aggTrade`` → :class:`Trade`."""
        local = local_now()
        # m=True: el buyer es maker → el agresor vendió.
        side = TradeSide.SELL if data.get("m") else TradeSide.BUY
        return Trade(
            symbol=self._symbol(data),
            provider=_PROVIDER,
            trade_id=str(data.get("t") or data.get("a") or ""),
            price=as_float(data.get("p"), "p"),
            size=as_float(data.get("q"), "q"),
            side=side,
            exchange_ts=ts_from_ms(data.get("T") or data.get("E") or 0),
            local_ts=local,
        )

    def _book_ticker(self, data: dict[str, Any]) -> Ticker:
        """``bookTicker`` → :class:`Ticker`."""
        local = local_now()
        # bookTicker spot no trae timestamp: se usa el local.
        exchange_ts = ts_from_ms(data["E"]) if "E" in data else local
        return Ticker(
            symbol=self._symbol(data),
            provider=_PROVIDER,
            bid=as_float(data.get("b"), "b"),
            ask=as_float(data.get("a"), "a"),
            bid_size=as_float(data.get("B", 0), "B"),
            ask_size=as_float(data.get("A", 0), "A"),
            exchange_ts=exchange_ts,
            local_ts=local,
        )

    def _depth(self, data: dict[str, Any]) -> OrderBookDelta:
        """``depthUpdate`` → :class:`OrderBookDelta` incremental."""
        return OrderBookDelta(
            symbol=self._symbol(data),
            provider=_PROVIDER,
            bids=_levels(data.get("b", ())),
            asks=_levels(data.get("a", ())),
            is_snapshot=False,
            first_sequence=int(data.get("U", 0)),
            last_sequence=int(data.get("u", 0)),
            exchange_ts=ts_from_ms(data.get("E") or 0),
            local_ts=local_now(),
        )

    def _kline(self, data: dict[str, Any]) -> Candle | None:
        """``kline`` → :class:`Candle` (``None`` si el timeframe es ajeno)."""
        k = data.get("k")
        if not isinstance(k, dict):
            return None
        try:
            timeframe = Timeframe(str(k.get("i")))
        except ValueError:
            return None
        volume = as_float(k.get("v", 0), "v")
        buy_volume = as_float(k.get("V", 0), "V")
        quote_volume = as_float(k.get("q", 0), "q")
        return Candle(
            symbol=self._symbol(data),
            provider=_PROVIDER,
            timeframe=timeframe,
            start=ts_from_ms(k.get("t") or 0),
            end=ts_from_ms(as_float(k.get("T", 0), "T") + 1),  # T es inclusivo
            open=as_float(k.get("o"), "o"),
            high=as_float(k.get("h"), "h"),
            low=as_float(k.get("l"), "l"),
            close=as_float(k.get("c"), "c"),
            volume=volume,
            buy_volume=buy_volume,
            sell_volume=max(volume - buy_volume, 0.0),
            vwap=quote_volume / volume if volume > 0 else 0.0,
            trades=int(k.get("n", 0)),
            closed=bool(k.get("x", False)),
            source="provider",
        )

    def _mark_price(self, data: dict[str, Any]) -> list[MarketObject]:
        """``markPriceUpdate`` → :class:`FundingRate` (+ mark/index)."""
        local = local_now()
        exchange_ts = ts_from_ms(data.get("E") or 0)
        next_funding = data.get("T")
        return [
            FundingRate(
                symbol=self._symbol(data),
                provider=_PROVIDER,
                rate=as_float(data.get("r", 0), "r"),
                next_funding_ts=ts_from_ms(next_funding) if next_funding else None,
                exchange_ts=exchange_ts,
                local_ts=local,
            )
        ]

    def _liquidation(self, data: dict[str, Any]) -> Liquidation:
        """``forceOrder`` → :class:`Liquidation`."""
        order = data.get("o") or {}
        side = TradeSide.BUY if str(order.get("S", "")).upper() == "BUY" else TradeSide.SELL
        return Liquidation(
            symbol=str(order.get("s", "")).upper(),
            provider=_PROVIDER,
            side=side,
            price=as_float(order.get("p", 0), "p"),
            size=as_float(order.get("q", 0), "q"),
            exchange_ts=ts_from_ms(order.get("T") or data.get("E") or 0),
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

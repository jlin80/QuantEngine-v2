"""Normalizador de OKX (API pública v5).

Canales soportados: ``trades``, ``tickers``, ``books``/``books5``,
``candle*``, ``funding-rate``, ``open-interest``, ``liquidation-orders``.

OKX usa ``BTC-USDT`` como instId; el símbolo interno elimina el guion.
"""

from typing import Any

from app.market.interfaces.normalizer import Normalizer
from app.market.models import (
    Candle,
    DepthLevel,
    FundingRate,
    Liquidation,
    MarketObject,
    OpenInterest,
    OrderBookDelta,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.normalizer.base import as_float, local_now, ts_from_ms

_PROVIDER = "okx"

_CANDLE_CHANNELS: dict[str, Timeframe] = {
    "candle1s": Timeframe.S1,
    "candle1m": Timeframe.M1,
    "candle3m": Timeframe.M3,
    "candle5m": Timeframe.M5,
    "candle15m": Timeframe.M15,
    "candle30m": Timeframe.M30,
    "candle1H": Timeframe.H1,
    "candle4H": Timeframe.H4,
    "candle1D": Timeframe.D1,
    "candle1W": Timeframe.W1,
    "candle1M": Timeframe.MN1,
}


def to_internal_symbol(inst_id: str) -> str:
    """OKX instId → símbolo interno (``BTC-USDT`` → ``BTCUSDT``)."""
    return inst_id.replace("-", "").upper()


def to_okx_inst_id(symbol: str, quote_hints: tuple[str, ...] = ("USDT", "USDC", "USD")) -> str:
    """Símbolo interno → instId de OKX (``BTCUSDT`` → ``BTC-USDT``).

    Args:
        symbol: Símbolo interno.
        quote_hints: Monedas de cotización a probar como sufijo.

    Returns:
        El instId con guion; si ninguna quote coincide, el símbolo tal cual.
    """
    upper = symbol.upper()
    for quote in quote_hints:
        if upper.endswith(quote) and len(upper) > len(quote):
            return f"{upper[: -len(quote)]}-{quote}"
    return upper


class OKXNormalizer(Normalizer):
    """Traductor de mensajes públicos de OKX v5 al modelo interno."""

    def normalize(self, message: dict[str, Any]) -> list[MarketObject]:
        """Translate one OKX v5 message into internal objects."""
        arg = message.get("arg") or {}
        channel = str(arg.get("channel", ""))
        data = message.get("data")
        if not channel or not isinstance(data, list):
            return []  # acks, pongs y errores
        inst_id = str(arg.get("instId", ""))
        symbol = to_internal_symbol(inst_id)
        if channel == "trades":
            return [self._trade(symbol, item) for item in data]
        if channel == "tickers":
            return [self._ticker(symbol, item) for item in data]
        if channel in ("books", "books5", "books50-l2-tbt", "books-l2-tbt"):
            action = str(message.get("action", "snapshot"))
            is_snapshot = channel == "books5" or action == "snapshot"
            return [self._book(symbol, item, is_snapshot=is_snapshot) for item in data]
        if channel in _CANDLE_CHANNELS:
            timeframe = _CANDLE_CHANNELS[channel]
            return [self._candle(symbol, timeframe, item) for item in data]
        if channel == "funding-rate":
            return [self._funding(symbol, item) for item in data]
        if channel == "open-interest":
            return [self._open_interest(symbol, item) for item in data]
        if channel == "liquidation-orders":
            return self._liquidations(data)
        return []

    def _trade(self, symbol: str, item: dict[str, Any]) -> Trade:
        """``trades`` item → :class:`Trade`."""
        side = TradeSide.BUY if str(item.get("side", "")).lower() == "buy" else TradeSide.SELL
        return Trade(
            symbol=symbol,
            provider=_PROVIDER,
            trade_id=str(item.get("tradeId", "")),
            price=as_float(item.get("px"), "px"),
            size=as_float(item.get("sz"), "sz"),
            side=side,
            exchange_ts=ts_from_ms(item.get("ts") or 0),
            local_ts=local_now(),
        )

    def _ticker(self, symbol: str, item: dict[str, Any]) -> Ticker:
        """``tickers`` item → :class:`Ticker`."""
        last = item.get("last")
        return Ticker(
            symbol=symbol,
            provider=_PROVIDER,
            bid=as_float(item.get("bidPx", 0), "bidPx"),
            ask=as_float(item.get("askPx", 0), "askPx"),
            bid_size=as_float(item.get("bidSz", 0), "bidSz"),
            ask_size=as_float(item.get("askSz", 0), "askSz"),
            last=as_float(last, "last") if last is not None else None,
            exchange_ts=ts_from_ms(item.get("ts") or 0),
            local_ts=local_now(),
        )

    def _book(self, symbol: str, item: dict[str, Any], *, is_snapshot: bool) -> OrderBookDelta:
        """``books*`` item → :class:`OrderBookDelta`."""
        sequence = int(item.get("seqId", 0))
        return OrderBookDelta(
            symbol=symbol,
            provider=_PROVIDER,
            bids=_levels(item.get("bids", ())),
            asks=_levels(item.get("asks", ())),
            is_snapshot=is_snapshot,
            first_sequence=int(item.get("prevSeqId", 0)) + 1 if not is_snapshot else 0,
            last_sequence=sequence,
            exchange_ts=ts_from_ms(item.get("ts") or 0),
            local_ts=local_now(),
        )

    def _candle(self, symbol: str, timeframe: Timeframe, item: list[Any]) -> Candle:
        """``candle*`` array → :class:`Candle`.

        Formato OKX: ``[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]``.
        """
        start = ts_from_ms(item[0])
        volume = as_float(item[5], "vol")
        quote_volume = as_float(item[7], "volCcyQuote") if len(item) > 7 else 0.0
        return Candle(
            symbol=symbol,
            provider=_PROVIDER,
            timeframe=timeframe,
            start=start,
            end=timeframe.bucket_end(start),
            open=as_float(item[1], "o"),
            high=as_float(item[2], "h"),
            low=as_float(item[3], "l"),
            close=as_float(item[4], "c"),
            volume=volume,
            vwap=quote_volume / volume if volume > 0 else 0.0,
            closed=len(item) > 8 and str(item[8]) == "1",
            source="provider",
        )

    def _funding(self, symbol: str, item: dict[str, Any]) -> FundingRate:
        """``funding-rate`` item → :class:`FundingRate`."""
        next_ts = item.get("nextFundingTime")
        return FundingRate(
            symbol=symbol,
            provider=_PROVIDER,
            rate=as_float(item.get("fundingRate", 0), "fundingRate"),
            next_funding_ts=ts_from_ms(next_ts) if next_ts else None,
            exchange_ts=ts_from_ms(item.get("ts") or 0),
            local_ts=local_now(),
        )

    def _open_interest(self, symbol: str, item: dict[str, Any]) -> OpenInterest:
        """``open-interest`` item → :class:`OpenInterest`."""
        oi_ccy = item.get("oiCcy")
        return OpenInterest(
            symbol=symbol,
            provider=_PROVIDER,
            contracts=as_float(item.get("oi", 0), "oi"),
            notional=as_float(oi_ccy, "oiCcy") if oi_ccy is not None else None,
            exchange_ts=ts_from_ms(item.get("ts") or 0),
            local_ts=local_now(),
        )

    def _liquidations(self, data: list[dict[str, Any]]) -> list[MarketObject]:
        """``liquidation-orders`` → lista de :class:`Liquidation`."""
        results: list[MarketObject] = []
        for entry in data:
            symbol = to_internal_symbol(str(entry.get("instId", "")))
            for detail in entry.get("details", ()):
                side_raw = str(detail.get("side", "")).lower()
                results.append(
                    Liquidation(
                        symbol=symbol,
                        provider=_PROVIDER,
                        side=TradeSide.BUY if side_raw == "buy" else TradeSide.SELL,
                        price=as_float(detail.get("bkPx", 0), "bkPx"),
                        size=as_float(detail.get("sz", 0), "sz"),
                        exchange_ts=ts_from_ms(detail.get("ts") or 0),
                        local_ts=local_now(),
                    )
                )
        return results


def _levels(raw: object) -> tuple[DepthLevel, ...]:
    """Parse OKX level arrays (``[price, size, ...]``) into DepthLevels."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(
        DepthLevel(price=as_float(pair[0], "price"), size=as_float(pair[1], "size"))
        for pair in raw
        if isinstance(pair, list | tuple) and len(pair) >= 2
    )

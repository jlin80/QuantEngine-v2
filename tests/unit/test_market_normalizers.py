"""Normalización: payloads reales de cada exchange → mismo modelo interno."""

from datetime import UTC, datetime

from app.market.models import (
    Candle,
    FundingRate,
    OrderBookDelta,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.normalizer import BinanceNormalizer, BybitNormalizer, OKXNormalizer
from app.market.normalizer.okx import to_internal_symbol, to_okx_inst_id

# ---------------------------------------------------------------------------
# Binance
# ---------------------------------------------------------------------------


def test_binance_trade():
    message = {
        "e": "trade",
        "E": 1752580800500,
        "s": "BTCUSDT",
        "t": 12345,
        "p": "65000.10",
        "q": "0.5",
        "T": 1752580800123,
        "m": True,
    }
    objs = BinanceNormalizer().normalize(message)
    assert len(objs) == 1
    trade = objs[0]
    assert isinstance(trade, Trade)
    assert trade.symbol == "BTCUSDT"
    assert trade.provider == "binance"
    assert trade.price == 65000.10
    assert trade.size == 0.5
    assert trade.side is TradeSide.SELL  # buyer-is-maker → agresor vendió
    assert trade.exchange_ts == datetime.fromtimestamp(1752580800.123, tz=UTC)
    assert trade.exchange_ts.tzinfo is not None
    assert trade.local_ts >= trade.exchange_ts


def test_binance_combined_stream_wrapper():
    message = {
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 1,
            "p": "100",
            "q": "1",
            "T": 1752580800000,
            "m": False,
        },
    }
    objs = BinanceNormalizer().normalize(message)
    assert len(objs) == 1
    assert isinstance(objs[0], Trade)
    assert objs[0].side is TradeSide.BUY


def test_binance_book_ticker():
    message = {"u": 400900217, "s": "BTCUSDT", "b": "64999.9", "B": "2", "a": "65000.1", "A": "3"}
    objs = BinanceNormalizer().normalize(message)
    assert len(objs) == 1
    ticker = objs[0]
    assert isinstance(ticker, Ticker)
    assert ticker.bid == 64999.9
    assert ticker.ask == 65000.1
    assert ticker.bid_size == 2.0


def test_binance_depth_update():
    message = {
        "e": "depthUpdate",
        "E": 1752580800000,
        "s": "BTCUSDT",
        "U": 157,
        "u": 160,
        "b": [["64999.0", "1.5"], ["64998.0", "0"]],
        "a": [["65001.0", "2.0"]],
    }
    objs = BinanceNormalizer().normalize(message)
    assert len(objs) == 1
    delta = objs[0]
    assert isinstance(delta, OrderBookDelta)
    assert delta.is_snapshot is False
    assert delta.first_sequence == 157
    assert delta.last_sequence == 160
    assert delta.bids[1].size == 0.0  # nivel a borrar


def test_binance_kline_closed():
    message = {
        "e": "kline",
        "E": 1752580860001,
        "s": "BTCUSDT",
        "k": {
            "t": 1752580800000,
            "T": 1752580859999,
            "s": "BTCUSDT",
            "i": "1m",
            "o": "64990",
            "c": "65010",
            "h": "65020",
            "l": "64980",
            "v": "12",
            "n": 100,
            "x": True,
            "q": "780120",
            "V": "7",
        },
    }
    objs = BinanceNormalizer().normalize(message)
    assert len(objs) == 1
    candle = objs[0]
    assert isinstance(candle, Candle)
    assert candle.timeframe is Timeframe.M1
    assert candle.closed is True
    assert candle.source == "provider"
    assert candle.buy_volume == 7.0
    assert candle.sell_volume == 5.0
    assert candle.vwap == 780120.0 / 12.0
    assert candle.end == datetime.fromtimestamp(1752580860.0, tz=UTC)


def test_binance_mark_price_produces_funding():
    message = {
        "e": "markPriceUpdate",
        "E": 1752580800000,
        "s": "BTCUSDT",
        "p": "65000",
        "i": "64990",
        "r": "0.0001",
        "T": 1752595200000,
    }
    objs = BinanceNormalizer().normalize(message)
    assert len(objs) == 1
    funding = objs[0]
    assert isinstance(funding, FundingRate)
    assert funding.rate == 0.0001
    assert funding.next_funding_ts == datetime.fromtimestamp(1752595200.0, tz=UTC)


def test_binance_control_messages_produce_nothing():
    assert BinanceNormalizer().normalize({"result": None, "id": 1}) == []


# ---------------------------------------------------------------------------
# Bybit
# ---------------------------------------------------------------------------


def test_bybit_public_trade():
    message = {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": 1752580800123,
        "data": [
            {
                "T": 1752580800100,
                "s": "BTCUSDT",
                "S": "Buy",
                "v": "0.25",
                "p": "65000.5",
                "i": "trade-1",
            },
        ],
    }
    objs = BybitNormalizer().normalize(message)
    assert len(objs) == 1
    trade = objs[0]
    assert isinstance(trade, Trade)
    assert trade.provider == "bybit"
    assert trade.side is TradeSide.BUY
    assert trade.price == 65000.5
    assert trade.trade_id == "trade-1"


def test_bybit_orderbook_snapshot_then_delta():
    normalizer = BybitNormalizer()
    snapshot = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "snapshot",
        "ts": 1752580800000,
        "data": {"s": "BTCUSDT", "b": [["64999", "1"]], "a": [["65001", "2"]], "u": 100},
    }
    delta = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "delta",
        "ts": 1752580801000,
        "data": {"s": "BTCUSDT", "b": [["64999", "0"]], "a": [], "u": 101},
    }
    snap_objs = normalizer.normalize(snapshot)
    delta_objs = normalizer.normalize(delta)
    assert isinstance(snap_objs[0], OrderBookDelta) and snap_objs[0].is_snapshot
    assert isinstance(delta_objs[0], OrderBookDelta) and not delta_objs[0].is_snapshot


def test_bybit_ticker_delta_without_bbo_is_skipped():
    message = {
        "topic": "tickers.BTCUSDT",
        "type": "delta",
        "ts": 1752580800000,
        "data": {"symbol": "BTCUSDT", "lastPrice": "65000"},
    }
    assert BybitNormalizer().normalize(message) == []


def test_bybit_pong_produces_nothing():
    assert BybitNormalizer().normalize({"op": "pong", "success": True}) == []


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------


def test_okx_trade():
    message = {
        "arg": {"channel": "trades", "instId": "BTC-USDT"},
        "data": [
            {
                "instId": "BTC-USDT",
                "tradeId": "9",
                "px": "65000",
                "sz": "0.1",
                "side": "sell",
                "ts": "1752580800123",
            }
        ],
    }
    objs = OKXNormalizer().normalize(message)
    assert len(objs) == 1
    trade = objs[0]
    assert isinstance(trade, Trade)
    assert trade.symbol == "BTCUSDT"  # sin guion
    assert trade.provider == "okx"
    assert trade.side is TradeSide.SELL


def test_okx_books_snapshot_and_update():
    normalizer = OKXNormalizer()
    snapshot = {
        "arg": {"channel": "books", "instId": "BTC-USDT"},
        "action": "snapshot",
        "data": [
            {
                "bids": [["64999", "1", "0", "1"]],
                "asks": [["65001", "2", "0", "1"]],
                "ts": "1752580800000",
                "seqId": 50,
            }
        ],
    }
    update = {
        "arg": {"channel": "books", "instId": "BTC-USDT"},
        "action": "update",
        "data": [
            {
                "bids": [],
                "asks": [["65001", "0", "0", "0"]],
                "ts": "1752580801000",
                "seqId": 51,
                "prevSeqId": 50,
            }
        ],
    }
    snap = normalizer.normalize(snapshot)[0]
    upd = normalizer.normalize(update)[0]
    assert isinstance(snap, OrderBookDelta) and snap.is_snapshot
    assert isinstance(upd, OrderBookDelta) and not upd.is_snapshot
    assert upd.first_sequence == 51  # prevSeqId+1


def test_okx_candle_channel():
    message = {
        "arg": {"channel": "candle1m", "instId": "BTC-USDT"},
        "data": [
            ["1752580800000", "64990", "65020", "64980", "65010", "12", "780000", "780120", "1"]
        ],
    }
    objs = OKXNormalizer().normalize(message)
    assert len(objs) == 1
    candle = objs[0]
    assert isinstance(candle, Candle)
    assert candle.timeframe is Timeframe.M1
    assert candle.closed is True
    assert candle.vwap == 780120.0 / 12.0


def test_okx_symbol_mapping_roundtrip():
    assert to_internal_symbol("BTC-USDT") == "BTCUSDT"
    assert to_okx_inst_id("BTCUSDT") == "BTC-USDT"
    assert to_okx_inst_id("XAUUSD") == "XAU-USD"


def test_okx_ack_produces_nothing():
    assert OKXNormalizer().normalize({"event": "subscribe", "arg": {}}) == []

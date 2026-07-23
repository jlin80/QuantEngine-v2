"""Métricas del OrderBook y propiedades de los modelos internos."""

from datetime import UTC, datetime, timedelta

from app.market.models import DepthLevel, OrderBook, Ticker

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _book(bids, asks) -> OrderBook:
    return OrderBook(
        symbol="BTCUSDT",
        provider="binance",
        bids=tuple(DepthLevel(price=p, size=s) for p, s in bids),
        asks=tuple(DepthLevel(price=p, size=s) for p, s in asks),
        exchange_ts=_TS,
        local_ts=_TS,
    )


def test_top_of_book_spread_mid():
    book = _book([(100.0, 2.0), (99.0, 5.0)], [(101.0, 1.0), (102.0, 4.0)])
    assert book.best_bid is not None and book.best_bid.price == 100.0
    assert book.best_ask is not None and book.best_ask.price == 101.0
    assert book.spread == 1.0
    assert book.mid == 100.5


def test_microprice_weights_by_opposite_size():
    book = _book([(100.0, 3.0)], [(101.0, 1.0)])
    # microprice = (bid*ask_size + ask*bid_size) / (bid_size + ask_size)
    assert book.microprice == (100.0 * 1.0 + 101.0 * 3.0) / 4.0


def test_imbalance_and_depth():
    book = _book([(100.0, 6.0), (99.0, 2.0)], [(101.0, 2.0)])
    bid_depth, ask_depth = book.depth(10)
    assert bid_depth == 8.0
    assert ask_depth == 2.0
    assert book.imbalance(10) == (8.0 - 2.0) / 10.0


def test_liquidity_within_pct():
    book = _book([(100.0, 1.0), (95.0, 9.0)], [(101.0, 2.0), (110.0, 7.0)])
    bid_liq, ask_liq = book.liquidity_within(2.0)  # ±2% del mid (100.5)
    assert bid_liq == 1.0  # 95 queda fuera
    assert ask_liq == 2.0  # 110 queda fuera


def test_book_pressure_sign():
    heavy_bids = _book([(100.0, 50.0)], [(101.0, 1.0)])
    heavy_asks = _book([(100.0, 1.0)], [(101.0, 50.0)])
    assert heavy_bids.book_pressure() > 0
    assert heavy_asks.book_pressure() < 0


def test_empty_book_is_safe():
    book = _book([], [])
    assert book.best_bid is None
    assert book.spread is None
    assert book.mid is None
    assert book.microprice is None
    assert book.imbalance() == 0.0
    assert book.book_pressure() == 0.0
    assert book.liquidity_within(1.0) == (0.0, 0.0)


def test_ticker_derived_fields():
    local = _TS + timedelta(milliseconds=150)
    ticker = Ticker(
        symbol="BTCUSDT",
        provider="binance",
        bid=100.0,
        ask=100.5,
        exchange_ts=_TS,
        local_ts=local,
    )
    assert ticker.mid == 100.25
    assert ticker.spread == 0.5
    assert round(ticker.spread_bps, 2) == round(0.5 / 100.25 * 10_000, 2)
    assert ticker.latency_ms == 150.0
    payload = ticker.to_dict()
    assert payload["latency_ms"] == 150.0
    assert payload["exchange_ts"] == _TS.isoformat()


def test_book_to_dict_truncates_levels():
    book = _book([(100.0 - i, 1.0) for i in range(20)], [(101.0 + i, 1.0) for i in range(20)])
    payload = book.to_dict(levels=5)
    assert len(payload["bids"]) == 5
    assert len(payload["asks"]) == 5
    assert payload["best_bid"] == 100.0

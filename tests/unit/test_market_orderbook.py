"""Reconstrucción del order book: snapshots, deltas y huecos de secuencia."""

from datetime import UTC, datetime

from app.market.models import DepthLevel, OrderBookDelta
from app.market.services.orderbook_manager import OrderBookEngine, OrderBookManager

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _delta(bids, asks, *, snapshot=False, first=0, last=0) -> OrderBookDelta:
    return OrderBookDelta(
        symbol="BTCUSDT",
        provider="binance",
        bids=tuple(DepthLevel(price=p, size=s) for p, s in bids),
        asks=tuple(DepthLevel(price=p, size=s) for p, s in asks),
        is_snapshot=snapshot,
        first_sequence=first,
        last_sequence=last,
        exchange_ts=_TS,
        local_ts=_TS,
    )


def test_snapshot_builds_sorted_book():
    engine = OrderBookEngine("BTCUSDT", "binance")
    book = engine.apply(
        _delta([(99.0, 1.0), (100.0, 2.0)], [(102.0, 3.0), (101.0, 1.0)], snapshot=True, last=10)
    )
    assert book is not None
    assert engine.synced
    assert [level.price for level in book.bids] == [100.0, 99.0]
    assert [level.price for level in book.asks] == [101.0, 102.0]
    assert book.sequence == 10


def test_delta_before_snapshot_is_ignored():
    engine = OrderBookEngine("BTCUSDT", "binance")
    assert engine.apply(_delta([(100.0, 1.0)], [], first=1, last=1)) is None
    assert not engine.synced


def test_incremental_update_and_level_deletion():
    engine = OrderBookEngine("BTCUSDT", "binance")
    engine.apply(_delta([(100.0, 2.0)], [(101.0, 1.0)], snapshot=True, last=10))
    book = engine.apply(_delta([(100.0, 0.0), (99.5, 4.0)], [(101.0, 2.0)], first=11, last=11))
    assert book is not None
    assert [level.price for level in book.bids] == [99.5]  # 100.0 eliminado
    assert book.asks[0].size == 2.0  # 101.0 actualizado
    assert book.sequence == 11


def test_sequence_gap_invalidates_book():
    engine = OrderBookEngine("BTCUSDT", "binance")
    engine.apply(_delta([(100.0, 1.0)], [(101.0, 1.0)], snapshot=True, last=10))
    result = engine.apply(_delta([(100.0, 2.0)], [], first=15, last=15))  # hueco 11-14
    assert result is None
    assert not engine.synced
    # Un snapshot nuevo lo repara.
    book = engine.apply(_delta([(100.0, 5.0)], [(101.0, 5.0)], snapshot=True, last=20))
    assert book is not None
    assert engine.synced


def test_old_delta_is_ignored_without_invalidating():
    engine = OrderBookEngine("BTCUSDT", "binance")
    engine.apply(_delta([(100.0, 1.0)], [(101.0, 1.0)], snapshot=True, last=10))
    assert engine.apply(_delta([(100.0, 9.0)], [], first=5, last=5)) is None
    assert engine.synced
    assert engine.sequence == 10


def test_trim_keeps_best_levels():
    engine = OrderBookEngine("BTCUSDT", "binance", max_depth=3)
    bids = [(100.0 - i, 1.0) for i in range(10)]
    asks = [(101.0 + i, 1.0) for i in range(10)]
    book = engine.apply(_delta(bids, asks, snapshot=True, last=1))
    assert book is not None
    assert len(book.bids) == 3
    assert book.bids[0].price == 100.0  # se conservan los mejores
    assert len(book.asks) == 3
    assert book.asks[0].price == 101.0


def test_manager_reports_resync_needed():
    manager = OrderBookManager()
    book, resync = manager.apply(_delta([(100.0, 1.0)], [(101.0, 1.0)], snapshot=True, last=10))
    assert book is not None and resync is False
    book, resync = manager.apply(_delta([(100.0, 2.0)], [], first=20, last=20))
    assert book is None and resync is True
    status = manager.status()
    assert status["BTCUSDT"]["synced"] is False

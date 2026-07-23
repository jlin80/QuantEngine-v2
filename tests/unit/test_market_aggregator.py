"""Conversión de trades en velas multi-timeframe."""

from datetime import UTC, datetime, timedelta

from app.market.aggregator import CandleAggregator
from app.market.models import Timeframe, Trade, TradeSide

_BASE = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)


def _trade(price: float, size: float, side: TradeSide, offset_s: float) -> Trade:
    ts = _BASE + timedelta(seconds=offset_s)
    return Trade(
        symbol="BTCUSDT",
        provider="binance",
        price=price,
        size=size,
        side=side,
        exchange_ts=ts,
        local_ts=ts,
    )


def test_candle_builds_ohlcv_vwap_and_sides():
    agg = CandleAggregator([Timeframe.M1])
    assert agg.add_trade(_trade(100.0, 1.0, TradeSide.BUY, 0)) == []
    assert agg.add_trade(_trade(105.0, 2.0, TradeSide.SELL, 10)) == []
    assert agg.add_trade(_trade(95.0, 1.0, TradeSide.BUY, 20)) == []

    closed = agg.add_trade(_trade(101.0, 1.0, TradeSide.BUY, 65))  # siguiente bucket
    assert len(closed) == 1
    candle = closed[0]
    assert candle.closed is True
    assert candle.open == 100.0
    assert candle.high == 105.0
    assert candle.low == 95.0
    assert candle.close == 95.0
    assert candle.volume == 4.0
    assert candle.buy_volume == 2.0
    assert candle.sell_volume == 2.0
    expected_vwap = (100.0 * 1 + 105.0 * 2 + 95.0 * 1) / 4.0
    assert candle.vwap == expected_vwap
    assert candle.trades == 3
    assert candle.start == _BASE
    assert candle.end == _BASE + timedelta(minutes=1)


def test_multiple_timeframes_close_independently():
    agg = CandleAggregator([Timeframe.M1, Timeframe.M5])
    agg.add_trade(_trade(100.0, 1.0, TradeSide.BUY, 0))
    closed = agg.add_trade(_trade(101.0, 1.0, TradeSide.BUY, 70))  # cierra 1m, no 5m
    assert [c.timeframe for c in closed] == [Timeframe.M1]

    closed = agg.add_trade(_trade(102.0, 1.0, TradeSide.BUY, 60 * 5 + 5))
    frames = {c.timeframe for c in closed}
    assert frames == {Timeframe.M1, Timeframe.M5}


def test_building_candle_is_partial_snapshot():
    agg = CandleAggregator([Timeframe.M1])
    agg.add_trade(_trade(100.0, 1.0, TradeSide.BUY, 0))
    building = agg.building_candle("BTCUSDT", Timeframe.M1)
    assert building is not None
    assert building.closed is False
    assert building.close == 100.0
    assert agg.building_candle("ETHUSDT", Timeframe.M1) is None


def test_flush_stale_closes_expired_buckets():
    agg = CandleAggregator([Timeframe.M1])
    agg.add_trade(_trade(100.0, 1.0, TradeSide.BUY, 0))
    assert agg.flush_stale(_BASE + timedelta(seconds=30)) == []  # aún vivo
    closed = agg.flush_stale(_BASE + timedelta(seconds=61))
    assert len(closed) == 1
    assert closed[0].closed is True
    assert agg.building_candle("BTCUSDT", Timeframe.M1) is None


def test_gap_between_trades_starts_fresh_bucket():
    agg = CandleAggregator([Timeframe.M1])
    agg.add_trade(_trade(100.0, 1.0, TradeSide.BUY, 0))
    closed = agg.add_trade(_trade(110.0, 1.0, TradeSide.BUY, 60 * 10))  # 10 min después
    assert len(closed) == 1  # cierra la vela vieja; no se inventan velas vacías
    building = agg.building_candle("BTCUSDT", Timeframe.M1)
    assert building is not None
    assert building.open == 110.0


def test_tick_timeframe_is_ignored():
    agg = CandleAggregator([Timeframe.TICK, Timeframe.M1])
    assert agg.timeframes == (Timeframe.M1,)

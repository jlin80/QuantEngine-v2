"""Conversión de timestamps y aritmética de buckets por timeframe."""

from datetime import UTC, datetime

import pytest
from app.market.models import Timeframe


def test_seconds_known_values():
    assert Timeframe.S1.seconds == 1
    assert Timeframe.M1.seconds == 60
    assert Timeframe.H4.seconds == 14_400
    assert Timeframe.D1.seconds == 86_400
    assert Timeframe.TICK.seconds is None
    assert Timeframe.MN1.seconds is None


def test_bucket_start_minute():
    moment = datetime(2026, 7, 15, 12, 34, 56, 789000, tzinfo=UTC)
    assert Timeframe.M1.bucket_start(moment) == datetime(2026, 7, 15, 12, 34, tzinfo=UTC)
    assert Timeframe.M5.bucket_start(moment) == datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
    assert Timeframe.M15.bucket_start(moment) == datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
    assert Timeframe.H1.bucket_start(moment) == datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    assert Timeframe.D1.bucket_start(moment) == datetime(2026, 7, 15, tzinfo=UTC)


def test_bucket_start_week_anchors_monday():
    # 2026-07-15 es miércoles → la semana empieza el lunes 13.
    moment = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    assert Timeframe.W1.bucket_start(moment) == datetime(2026, 7, 13, tzinfo=UTC)


def test_bucket_start_month():
    moment = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    assert Timeframe.MN1.bucket_start(moment) == datetime(2026, 7, 1, tzinfo=UTC)


def test_bucket_end():
    start = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
    assert Timeframe.M5.bucket_end(start) == datetime(2026, 7, 15, 12, 35, tzinfo=UTC)
    assert Timeframe.MN1.bucket_end(datetime(2026, 12, 1, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )


def test_bucket_start_converts_to_utc():
    from datetime import timedelta, timezone

    plus_two = timezone(timedelta(hours=2))
    moment = datetime(2026, 7, 15, 14, 3, tzinfo=plus_two)  # 12:03 UTC
    start = Timeframe.M1.bucket_start(moment)
    assert start == datetime(2026, 7, 15, 12, 3, tzinfo=UTC)
    assert start.tzinfo == UTC


def test_tick_has_no_bucket():
    with pytest.raises(ValueError, match="TICK"):
        Timeframe.TICK.bucket_start(datetime(2026, 7, 15, tzinfo=UTC))
    with pytest.raises(ValueError, match="TICK"):
        Timeframe.TICK.bucket_end(datetime(2026, 7, 15, tzinfo=UTC))

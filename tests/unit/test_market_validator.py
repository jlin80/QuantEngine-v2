"""Validador de calidad de datos: duplicados, desorden, gaps, corruptos."""

from datetime import UTC, datetime, timedelta

from app.market.models import (
    Candle,
    QualityIssueType,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.validator import DataValidator

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _trade(price=100.0, size=1.0, trade_id="", ts=_TS, local=None) -> Trade:
    return Trade(
        symbol="BTCUSDT",
        provider="binance",
        trade_id=trade_id,
        price=price,
        size=size,
        side=TradeSide.BUY,
        exchange_ts=ts,
        local_ts=local or ts,
    )


def _issues_of(issues) -> set[QualityIssueType]:
    return {issue.issue for issue in issues}


def test_clean_trade_passes():
    validator = DataValidator()
    assert validator.validate_trade(_trade()) == []


def test_negative_price_discarded():
    validator = DataValidator()
    issues = validator.validate_trade(_trade(price=-5.0))
    assert QualityIssueType.NEGATIVE_PRICE in _issues_of(issues)
    assert validator.should_discard(issues)


def test_duplicate_trade_id_discarded():
    validator = DataValidator()
    assert validator.validate_trade(_trade(trade_id="t1")) == []
    issues = validator.validate_trade(_trade(trade_id="t1"))
    assert QualityIssueType.DUPLICATE in _issues_of(issues)


def test_out_of_order_discarded():
    validator = DataValidator(out_of_order_grace=1.0)
    assert validator.validate_trade(_trade(ts=_TS)) == []
    issues = validator.validate_trade(_trade(ts=_TS - timedelta(seconds=5)))
    assert QualityIssueType.OUT_OF_ORDER in _issues_of(issues)


def test_small_backstep_within_grace_is_tolerated():
    validator = DataValidator(out_of_order_grace=2.0)
    assert validator.validate_trade(_trade(ts=_TS)) == []
    assert validator.validate_trade(_trade(ts=_TS - timedelta(seconds=1))) == []


def test_future_timestamp_discarded():
    validator = DataValidator(future_tolerance_seconds=5.0)
    issues = validator.validate_trade(_trade(ts=_TS + timedelta(seconds=30), local=_TS))
    assert QualityIssueType.FUTURE_TIMESTAMP in _issues_of(issues)


def test_stale_timestamp_flagged_but_kept():
    validator = DataValidator(stale_seconds=60.0)
    issues = validator.validate_trade(_trade(ts=_TS, local=_TS + timedelta(seconds=300)))
    assert QualityIssueType.STALE_TIMESTAMP in _issues_of(issues)
    assert not validator.should_discard(issues)


def test_price_gap_discarded():
    validator = DataValidator(max_price_jump_pct=10.0)
    assert validator.validate_trade(_trade(price=100.0)) == []
    later = _TS + timedelta(seconds=1)
    issues = validator.validate_trade(_trade(price=150.0, ts=later))
    assert QualityIssueType.PRICE_GAP in _issues_of(issues)


def test_impossible_volume_discarded():
    validator = DataValidator(max_volume=1000.0)
    issues = validator.validate_trade(_trade(size=10_000.0))
    assert QualityIssueType.IMPOSSIBLE_VOLUME in _issues_of(issues)


def test_crossed_ticker_discarded():
    validator = DataValidator()
    ticker = Ticker(
        symbol="BTCUSDT",
        provider="binance",
        bid=101.0,
        ask=100.0,
        exchange_ts=_TS,
        local_ts=_TS,
    )
    issues = validator.validate_ticker(ticker)
    assert QualityIssueType.CROSSED_BOOK in _issues_of(issues)


def test_malformed_candle_discarded():
    validator = DataValidator()
    candle = Candle(
        symbol="BTCUSDT",
        provider="binance",
        timeframe=Timeframe.M1,
        start=_TS,
        end=_TS + timedelta(minutes=1),
        open=100.0,
        high=99.0,  # high < open → malformada
        low=98.0,
        close=99.5,
        volume=1.0,
    )
    issues = validator.validate_candle(candle)
    assert QualityIssueType.MALFORMED_CANDLE in _issues_of(issues)


def test_naive_timestamp_discarded():
    validator = DataValidator()
    naive = datetime(2026, 7, 15, 12, 0)  # sin tzinfo
    issues = validator.validate_trade(_trade(ts=naive, local=naive))
    assert QualityIssueType.INVALID_TIMESTAMP in _issues_of(issues)


def test_stats_counters():
    validator = DataValidator()
    validator.validate_trade(_trade())
    validator.validate_trade(_trade(price=-1.0))
    stats = validator.stats
    assert stats["checked"] == 2
    assert stats["discarded"] == 1
    assert stats[QualityIssueType.NEGATIVE_PRICE.value] == 1

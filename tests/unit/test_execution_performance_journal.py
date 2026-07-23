"""Performance Engine y Trade Journal."""

from datetime import timedelta

from app.execution.journal import TradeJournal
from app.execution.models import ExitReason, PositionSide, TradeRecord
from app.execution.performance import PerformanceEngine
from app.utils.time import utc_now


def _trade(pnl: float, r: float = 0.0, minutes: int = 10) -> TradeRecord:
    entry = utc_now()
    return TradeRecord(
        position_id="p",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=1.0,
        entry_time=entry,
        exit_time=entry + timedelta(minutes=minutes),
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        pnl_gross=pnl,
        r_multiple=r,
        exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
    )


def test_empty_report_is_all_zeros():
    report = PerformanceEngine(10_000.0).compute([])
    assert report.total_trades == 0 and report.profit_factor == 0.0


def test_metrics_are_consistent():
    trades = [_trade(30.0, 1.5), _trade(-10.0, -1.0), _trade(20.0, 1.0), _trade(-10.0, -1.0)]
    report = PerformanceEngine(10_000.0).compute(trades)
    assert report.total_trades == 4
    assert report.wins == 2 and report.losses == 2
    assert report.win_rate == 0.5
    assert report.gross_profit == 50.0 and report.gross_loss == 20.0
    assert report.profit_factor == 2.5
    assert report.net_profit == 30.0
    assert report.expectancy == 7.5
    assert report.average_win == 25.0 and report.average_loss == 10.0
    assert report.risk_reward == 2.5
    assert report.max_drawdown > 0.0
    assert report.recovery_factor > 0.0


def test_drawdown_and_calmar():
    # Sube y luego cae: hay drawdown medible.
    trades = [_trade(100.0), _trade(-40.0), _trade(-40.0)]
    report = PerformanceEngine(10_000.0).compute(trades)
    assert report.max_drawdown == 80.0
    assert report.calmar != 0.0
    assert report.ulcer_index >= 0.0


def test_journal_records_and_queries():
    journal = TradeJournal(None, persist=False)
    journal.record(_trade(5.0))
    journal.record(_trade(-3.0))
    assert journal.count == 2
    assert len(journal.for_symbol("BTCUSDT")) == 2
    assert len(journal.recent(1)) == 1
    assert journal.status()["persist"] is False

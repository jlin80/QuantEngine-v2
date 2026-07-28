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


def test_trade_record_roundtrips_through_dict():
    """`from_dict` debe ser el inverso exacto de `to_dict`."""
    original = _trade(12.5, 1.25, minutes=7)

    restored = TradeRecord.from_dict(original.to_dict())

    assert restored.trade_id == original.trade_id
    assert restored.symbol == original.symbol
    assert restored.side is original.side
    assert restored.pnl == original.pnl
    assert restored.r_multiple == original.r_multiple
    assert restored.exit_reason is original.exit_reason
    assert restored.entry_time == original.entry_time
    assert restored.exit_time == original.exit_time


def test_journal_reloads_itself_from_disk(tmp_path):
    """El historial debe sobrevivir a un reinicio.

    Antes solo se releia via el servicio de recuperacion de la Fase 9, que esta
    apagado por defecto: cada reinicio dejaba Operations y las metricas vacias
    aunque el fichero JSONL siguiera creciendo.
    """
    path = tmp_path / "journal.jsonl"
    first = TradeJournal(path, persist=True)
    first.record(_trade(30.0, 1.5))
    first.record(_trade(-10.0, -1.0))

    # Nuevo proceso: journal vacio apuntando al mismo fichero.
    second = TradeJournal(path, persist=True)
    assert second.count == 0

    loaded = second.load_from_disk()

    assert loaded == 2
    assert second.count == 2
    assert [t.pnl for t in second.all()] == [30.0, -10.0]
    # Y el Performance Engine, que calcula sobre el journal, ya no ve la nada.
    assert PerformanceEngine(10_000.0).compute(second.all()).total_trades == 2


def test_journal_reload_is_idempotent(tmp_path):
    """Cargar dos veces no duplica el historial."""
    path = tmp_path / "journal.jsonl"
    first = TradeJournal(path, persist=True)
    first.record(_trade(5.0))

    second = TradeJournal(path, persist=True)
    assert second.load_from_disk() == 1
    assert second.load_from_disk() == 0  # ya tiene datos: no recarga
    assert second.count == 1


def test_journal_reload_skips_corrupt_lines(tmp_path):
    """Una linea corrupta no puede impedir cargar el resto."""
    path = tmp_path / "journal.jsonl"
    first = TradeJournal(path, persist=True)
    first.record(_trade(7.0))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{esto no es json}\n")
    first.record(_trade(-3.0))

    second = TradeJournal(path, persist=True)

    assert second.load_from_disk() == 2
    assert [t.pnl for t in second.all()] == [7.0, -3.0]


def test_journal_without_persistence_loads_nothing(tmp_path):
    """Sin fichero configurado la recarga es un no-op."""
    assert TradeJournal(None, persist=False).load_from_disk() == 0

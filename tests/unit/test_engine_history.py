"""Historial de señales/decisiones: nada se borra, todo se persiste."""

import json
from pathlib import Path

from app.config.settings import DatabaseSettings, QuantHistorySettings
from app.database.engine import DatabaseManager
from app.engine.models import Decision, DecisionAction, SignalRecord, SignalStatus
from app.engine.state_manager import HistoryWriter, SignalHistoryStore
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_signal


def _decision(symbol: str = "BTCUSDT", accepted: bool = True) -> Decision:
    return Decision(
        symbol=symbol,
        timestamp=utc_now(),
        action=DecisionAction.OPEN_LONG if accepted else DecisionAction.STAND_ASIDE,
        accepted=accepted,
        score=80.0,
        confidence=0.7,
        agreement=1.0,
    )


def _writer(tmp_path: Path, *, persist: bool = True) -> HistoryWriter:
    # Puerto 1: conexión rechazada al instante -> simula DB caída.
    db = DatabaseManager(DatabaseSettings(host="127.0.0.1", port=1, name="nope"))
    settings = QuantHistorySettings(
        persist=persist,
        flush_interval_seconds=3600.0,
        batch_size=100,
        spill_dir=tmp_path / "spill",
    )
    return HistoryWriter(db, settings)


def test_every_status_is_retained_and_counted():
    store = SignalHistoryStore()
    for status in (
        SignalStatus.ACCEPTED,
        SignalStatus.REJECTED,
        SignalStatus.EXPIRED,
        SignalStatus.SUPERSEDED,
    ):
        store.record_signal(make_signal(strategy="alpha"), status, (status.value,))

    assert len(store.signals()) == 4
    counters = store.strategy_counters()["alpha"]
    assert counters[SignalStatus.ACCEPTED.value] == 1
    assert counters[SignalStatus.REJECTED.value] == 1
    assert counters[SignalStatus.EXPIRED.value] == 1
    assert counters[SignalStatus.SUPERSEDED.value] == 1


def test_performance_factor_needs_sample_then_uses_acceptance():
    store = SignalHistoryStore()
    assert store.performance_factor("nueva") == 0.5, "sin historial: neutro"

    for _ in range(4):
        store.record_signal(make_signal(strategy="alpha"), SignalStatus.ACCEPTED)
    store.record_signal(make_signal(strategy="alpha"), SignalStatus.REJECTED)

    assert store.performance_factor("alpha") == 0.8  # 4 de 5 aceptadas


def test_decisions_filter_by_symbol_and_runtime_state():
    store = SignalHistoryStore()
    store.record_decision(_decision("BTCUSDT"))
    store.record_decision(_decision("XAUUSD"))

    assert len(store.decisions()) == 2
    assert [d.symbol for d in store.decisions(symbol="xauusd")] == ["XAUUSD"]

    store.set_state("daily_drawdown_pct", 3.2)
    assert store.get_state("daily_drawdown_pct") == 3.2


def test_memory_ring_keeps_most_recent():
    store = SignalHistoryStore(memory_limit=3)
    for i in range(5):
        store.record_signal(make_signal(score=float(i)), SignalStatus.EXPIRED)
    scores = [r.signal.score for r in store.signals()]
    assert scores == [2.0, 3.0, 4.0]


def test_signal_sink_receives_every_record(tmp_path: Path):
    writer = _writer(tmp_path)
    store = SignalHistoryStore(signal_sink=writer.add_signal)

    store.record_signal(make_signal(), SignalStatus.REJECTED, ("razón",))
    store.record_signal(make_signal(), SignalStatus.ACCEPTED)

    assert writer.status()["pending_signals"] == 2


def test_broken_sink_never_breaks_recording():
    def sink(record: SignalRecord) -> None:
        raise RuntimeError("sink caído")

    store = SignalHistoryStore(signal_sink=sink)
    record = store.record_signal(make_signal(), SignalStatus.ACCEPTED)
    assert record.status is SignalStatus.ACCEPTED
    assert len(store.signals()) == 1


async def test_writer_spills_to_jsonl_when_db_is_down(tmp_path: Path):
    writer = _writer(tmp_path)
    store = SignalHistoryStore(signal_sink=writer.add_signal)
    store.record_signal(make_signal(), SignalStatus.ACCEPTED, ("ok",))
    writer.add_decision(_decision())

    await writer.flush()

    status = writer.status()
    assert status["db_available"] is False
    assert status["rows_spilled"] == 2
    spill_files = list((tmp_path / "spill").glob("engine-*.jsonl"))
    assert len(spill_files) == 2  # signals y decisions
    for path in spill_files:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert lines and json.loads(lines[0])


def test_disabled_persistence_ignores_everything(tmp_path: Path):
    writer = _writer(tmp_path, persist=False)
    store = SignalHistoryStore(signal_sink=writer.add_signal)
    store.record_signal(make_signal(), SignalStatus.ACCEPTED)
    writer.add_decision(_decision())
    assert writer.status()["pending_signals"] == 0
    assert writer.status()["pending_decisions"] == 0

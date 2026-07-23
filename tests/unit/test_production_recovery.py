"""Recuperación: tras un reinicio el motor nunca empieza de cero."""

from pathlib import Path

from app.engine.events import DecisionGenerated
from app.execution.execution_engine import ExecutionEngine
from app.market.services import MarketDataService, MarketStateStore
from app.production.recovery import RecoveryService, StateSnapshotStore

from tests.unit.execution_helpers import (
    make_engine,
    make_execution_settings,
    make_market_with_state,
)
from tests.unit.production_helpers import make_recovery_settings
from tests.unit.quant_helpers import make_candles, make_ticker


def _decision() -> DecisionGenerated:
    return DecisionGenerated(
        source="test",
        decision_id="d1",
        symbol="BTCUSDT",
        action="open_long",
        accepted=True,
        score=80.0,
        confidence=0.8,
        summary="momentum",
    )


def _market() -> tuple[MarketDataService, MarketStateStore]:
    return make_market_with_state(
        candles=make_candles([100.0, 101.0, 100.5, 101.5, 102.0] * 6),
        ticker=make_ticker(bid=101.9, ask=101.95),
    )


def _recovery(tmp_path: Path, engine: ExecutionEngine, **overrides: object) -> RecoveryService:
    settings = make_recovery_settings(tmp_path, **overrides)
    return RecoveryService(settings, StateSnapshotStore(settings.snapshot_path), engine)


async def test_open_positions_survive_a_restart(tmp_path):
    market, _ = _market()
    first = make_engine(market)
    await first.process_decision(_decision())
    assert len(first.positions.open_positions) == 1
    original = first.positions.open_positions[0]

    _recovery(tmp_path, first).capture()

    # Proceso nuevo: motor fresco, mismo snapshot.
    market2, _ = _market()
    second = make_engine(market2)
    assert not second.positions.open_positions
    report = await _recovery(tmp_path, second).restore()

    assert report["positions_restored"] == 1
    restored = second.positions.open_positions[0]
    assert restored.position_id == original.position_id
    assert restored.entry_price == original.entry_price
    assert restored.stop_loss == original.stop_loss


async def test_trailing_state_is_not_lost(tmp_path):
    """Los extremos de precio no están en Position.to_dict(): se serializan aparte."""
    market, _ = _market()
    engine = make_engine(market)
    await engine.process_decision(_decision())
    position = engine.positions.open_positions[0]
    position.update_mark(150.0)
    position.update_mark(90.0)
    _recovery(tmp_path, engine).capture()

    market2, _ = _market()
    second = make_engine(market2)
    await _recovery(tmp_path, second).restore()
    restored = second.positions.open_positions[0]
    assert restored.highest_price == 150.0
    assert restored.lowest_price == 90.0


async def test_accounting_and_peak_equity_are_restored(tmp_path):
    market, state = _market()
    engine = make_engine(market)
    await engine.process_decision(_decision())
    state.update_ticker(make_ticker(bid=500.0, ask=500.05))
    await engine.manage_once()
    assert engine.portfolio.total_trades == 1
    balance, peak = engine.portfolio.balance, engine.portfolio.peak_equity

    _recovery(tmp_path, engine).capture()

    market2, _ = _market()
    second = make_engine(market2)
    await _recovery(tmp_path, second).restore()
    assert second.portfolio.balance == balance
    assert second.portfolio.peak_equity == peak
    assert second.portfolio.total_trades == 1


async def test_kill_switch_state_is_restored(tmp_path):
    market, _ = _market()
    engine = make_engine(market)
    engine.risk.engage_kill_switch("drawdown")
    _recovery(tmp_path, engine).capture()

    market2, _ = _market()
    second = make_engine(market2)
    report = await _recovery(tmp_path, second).restore()
    assert report["kill_switch_restored"]
    assert second.risk.kill_switch_active


async def test_journal_is_reloaded_from_disk(tmp_path):
    """El journal escribía pero nunca releía: el histórico arrancaba vacío."""
    journal_path = tmp_path / "journal.jsonl"
    settings = make_execution_settings(persist_journal=True, journal_path=journal_path)
    market, state = _market()
    engine = make_engine(market, settings)
    await engine.process_decision(_decision())
    state.update_ticker(make_ticker(bid=500.0, ask=500.05))
    await engine.manage_once()
    assert engine.journal.count == 1

    market2, _ = _market()
    second = make_engine(market2, settings)
    assert second.journal.count == 0  # deque fresco
    report = await _recovery(tmp_path, second).restore()
    assert report["trades_restored"] == 1
    assert second.journal.count == 1


async def test_stale_snapshot_is_ignored(tmp_path):
    """Rehidratar posiciones de hace días sería peor que ignorarlas."""
    market, _ = _market()
    engine = make_engine(market)
    await engine.process_decision(_decision())
    _recovery(tmp_path, engine).capture()

    market2, _ = _market()
    second = make_engine(market2)
    # Ventana de microsegundos: el snapshot que acabamos de tomar ya es "viejo".
    report = await _recovery(tmp_path, second, max_age_hours=1e-9).restore()
    assert report["positions_restored"] == 0
    assert "viejo" in report["skipped_reason"]
    assert not second.positions.open_positions


async def test_missing_snapshot_is_not_an_error(tmp_path):
    market, _ = _market()
    engine = make_engine(market)
    report = await _recovery(tmp_path, engine).restore()
    assert report["positions_restored"] == 0
    assert report["skipped_reason"] == "no hay snapshot previo"


async def test_corrupt_snapshot_degrades_cleanly(tmp_path):
    (tmp_path / "state.json").write_text("{ esto no es json", encoding="utf-8")
    market, _ = _market()
    engine = make_engine(market)
    report = await _recovery(tmp_path, engine).restore()
    assert report["positions_restored"] == 0
    assert not engine.positions.open_positions

"""Framework de Evaluación Continua: operaciones virtuales y estadísticas."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from app.config.settings import QuantEvaluationSettings
from app.engine.evaluation import PerformanceTracker
from app.engine.models import Direction, EntryZone, SignalStatus
from app.engine.state_manager import SignalHistoryStore
from app.market.services import MarketDataService
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_candles, make_market, make_signal


def _settings(tmp_path: Path, **overrides: object) -> QuantEvaluationSettings:
    base: dict[str, object] = {
        "enabled": True,
        "timeframe": "1m",
        "check_interval_seconds": 3600.0,
        "max_holding_minutes": 60.0,
        "false_signal_bars": 3,
        "snapshot_path": tmp_path / "stats.json",
    }
    base.update(overrides)
    return QuantEvaluationSettings(**base)  # type: ignore[arg-type]


def _tracker(tmp_path: Path, market: MarketDataService, **overrides: object) -> PerformanceTracker:
    return PerformanceTracker(_settings(tmp_path, **overrides), market)


def _record(tracker: PerformanceTracker, **signal_kwargs: object) -> None:
    history = SignalHistoryStore(signal_sink=tracker.on_signal_record)
    history.record_signal(make_signal(**signal_kwargs), SignalStatus.ACCEPTED)  # type: ignore[arg-type]


def _levels(direction: Direction = Direction.LONG) -> dict[str, object]:
    if direction is Direction.LONG:
        return {
            "direction": direction,
            "entry_zone": EntryZone(low=99.9, high=100.1),  # entry 100
            "stop_loss": 99.0,  # riesgo 1.0
            "take_profit": 102.0,  # recompensa 2.0
        }
    return {
        "direction": direction,
        "entry_zone": EntryZone(low=99.9, high=100.1),
        "stop_loss": 101.0,
        "take_profit": 98.0,
    }


def test_signals_without_levels_are_counted_but_not_tracked(tmp_path: Path):
    tracker = _tracker(tmp_path, make_market())
    _record(tracker, strategy="alpha")  # sin stop/entry no hay operación virtual
    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.signals == 1
    assert perf.tracked == 0


def test_dedupe_by_signal_id(tmp_path: Path):
    tracker = _tracker(tmp_path, make_market())
    signal = make_signal(**_levels())
    history = SignalHistoryStore(signal_sink=tracker.on_signal_record)
    history.record_signal(signal, SignalStatus.ACCEPTED)
    history.record_signal(signal, SignalStatus.SUPERSEDED)  # mismo id: ignorado
    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.signals == 1
    assert perf.tracked == 1


def test_take_profit_resolution_wins_in_r(tmp_path: Path):
    opened = utc_now() - timedelta(minutes=30)
    candles = make_candles([100.0, 100.5, 102.5], start=opened, range_pad=0.1)
    tracker = _tracker(tmp_path, make_market(candles=candles))
    _record(tracker, timestamp=opened, **_levels())

    resolved = tracker.evaluate_open()

    assert resolved == 1
    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.wins == 1 and perf.losses == 0
    assert perf.expectancy_r == pytest.approx(2.0)
    assert perf.win_rate == 1.0
    assert perf.avg_holding_seconds is not None and perf.avg_holding_seconds > 0


def test_stop_loss_conservative_when_both_hit(tmp_path: Path):
    opened = utc_now() - timedelta(minutes=30)
    # La vela toca el stop Y el target: regla conservadora = pérdida.
    candles = make_candles([100.0], start=opened, highs=[102.5], lows=[98.5])
    tracker = _tracker(tmp_path, make_market(candles=candles))
    _record(tracker, timestamp=opened, **_levels())

    tracker.evaluate_open()

    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.losses == 1 and perf.wins == 0
    assert perf.expectancy_r == pytest.approx(-1.0)
    assert perf.false_signals == 1, "el stop en las primeras velas es falsa señal"


def test_short_direction_resolution(tmp_path: Path):
    opened = utc_now() - timedelta(minutes=30)
    candles = make_candles([100.0, 99.0, 97.8], start=opened, range_pad=0.1)
    tracker = _tracker(tmp_path, make_market(candles=candles))
    _record(tracker, timestamp=opened, **_levels(Direction.SHORT))

    tracker.evaluate_open()

    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.wins == 1
    assert perf.expectancy_r == pytest.approx(2.0)


def test_timeout_marks_to_market(tmp_path: Path):
    opened = utc_now() - timedelta(minutes=120)  # más allá del max_holding (60)
    candles = make_candles([100.0, 100.4, 100.5], start=opened, range_pad=0.1)
    tracker = _tracker(tmp_path, make_market(candles=candles))
    _record(tracker, timestamp=opened, **_levels())

    tracker.evaluate_open()

    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.timeouts == 1
    assert perf.expectancy_r == pytest.approx(0.5)  # +0.5R a mercado


def test_profit_factor_and_drawdown(tmp_path: Path):
    opened = utc_now() - timedelta(minutes=30)
    win = make_candles([100.0, 102.5], start=opened, range_pad=0.1)
    loss = make_candles([100.0, 98.9], start=opened, range_pad=0.05)
    tracker = _tracker(tmp_path, make_market(candles=win))
    _record(tracker, strategy="alpha", timestamp=opened, **_levels())
    tracker.evaluate_open()
    # Segunda señal contra un mercado perdedor.
    tracker._market = make_market(candles=loss)  # acceso deliberado en test
    _record(tracker, strategy="alpha", timestamp=opened, **_levels())
    tracker.evaluate_open()

    perf = tracker.performance("alpha")
    assert perf is not None
    assert perf.evaluated == 2
    assert perf.profit_factor == pytest.approx(2.0)  # +2R / -1R
    assert perf.max_drawdown_r == pytest.approx(1.0)
    assert perf.win_rate == pytest.approx(0.5)


def test_factor_requires_sample_then_tracks_expectancy(tmp_path: Path):
    opened = utc_now() - timedelta(minutes=30)
    candles = make_candles([100.0, 102.5], start=opened, range_pad=0.1)
    tracker = _tracker(tmp_path, make_market(candles=candles))
    assert tracker.factor("alpha") == 0.5, "sin muestra: neutro"
    for _ in range(12):
        _record(tracker, strategy="alpha", timestamp=opened, **_levels())
        tracker.evaluate_open()
    assert tracker.factor("alpha") > 0.5, "expectativa positiva sube la relevancia"


def test_snapshot_to_disk(tmp_path: Path):
    tracker = _tracker(tmp_path, make_market())
    _record(tracker, **_levels())
    tracker.snapshot_to_disk()
    payload = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert payload["strategies"]["alpha"]["signals"] == 1
    assert "generated_at" in payload


def test_disabled_tracker_ignores_everything(tmp_path: Path):
    tracker = _tracker(tmp_path, make_market(), enabled=False)
    _record(tracker, **_levels())
    assert tracker.status()["strategies"] == {}

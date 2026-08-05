"""Data Quality Engine (Bloque 11): señales, score y reducción de riesgo."""

import pytest
from app.config.settings import DataQualitySettings
from app.monitoring.data_quality import SIGNALS, DataQualityEngine, DataQualityInputs
from app.utils.time import use_clock


def _engine(inputs: DataQualityInputs, **overrides) -> DataQualityEngine:
    settings = DataQualitySettings(**overrides)
    return DataQualityEngine(settings, lambda: inputs)


def _healthy(**overrides) -> DataQualityInputs:
    defaults = {
        "messages": 10_000,
        "rejected": 0,
        "dropped": 0,
        "expected_symbols": 2,
        "symbols_with_data": 2,
        "books_synced": 2,
        "books_total": 2,
        "max_timestamp_drift_seconds": 0.0,
        "exchange_lag_ms": 50.0,
    }
    return DataQualityInputs(**{**defaults, **overrides})


def test_a_healthy_feed_scores_high_and_does_not_touch_risk() -> None:
    report = _engine(_healthy()).measure()
    assert report.score is not None and report.score > 90.0
    assert report.risk_multiplier == 1.0
    assert report.degraded is False
    assert set(report.signals) == set(SIGNALS)


def test_a_blind_engine_is_caught_by_the_missing_data_signal() -> None:
    # La lección del 04/08: el motor puede quedarse ciego sin un solo error.
    report = _engine(_healthy(symbols_with_data=0)).measure()
    assert report.signals["missing_data"] == 0.0
    assert report.degraded is True
    assert report.risk_multiplier < 1.0


def test_rejected_ticks_lower_tick_quality() -> None:
    report = _engine(_healthy(rejected=9_000)).measure()
    assert report.signals["tick_quality"] == pytest.approx(0.1)
    assert report.degraded is True


def test_dropped_messages_lower_packet_loss_quality() -> None:
    report = _engine(_healthy(dropped=5_000)).measure()
    assert report.signals["packet_loss"] == pytest.approx(0.5)


def test_no_messages_is_absence_of_measurement_not_zero_quality() -> None:
    # Son cosas distintas: el motor ciego se detecta con `missing_data`, no
    # fingiendo una calidad pésima aquí.
    report = _engine(_healthy(messages=0)).measure()
    assert "feed_quality" in report.missing
    assert "feed_quality" not in report.signals


def test_a_provider_without_order_book_is_declared_not_penalised() -> None:
    report = _engine(_healthy(books_synced=None, books_total=0)).measure()
    assert "orderbook_quality" in report.missing
    assert report.risk_multiplier == 1.0


def test_clock_drift_is_measured_even_without_a_single_datum() -> None:
    # Es la señal del incidente y no depende de que llegue ningún dato.
    report = _engine(DataQualityInputs()).measure()
    assert "clock_drift" in report.signals


def test_a_leaked_simulated_clock_destroys_the_clock_signal() -> None:
    from datetime import UTC, datetime, timedelta

    def _frozen() -> datetime:
        return datetime.now(UTC) + timedelta(hours=4)

    engine = _engine(_healthy(), max_clock_skew_seconds=5.0)
    with use_clock(_frozen):
        report = engine.measure()
    assert report.signals["clock_drift"] == 0.0
    assert report.degraded is True


def test_exchange_lag_degrades_gradually_not_as_a_cliff() -> None:
    fast = _engine(_healthy(exchange_lag_ms=100.0), max_exchange_lag_ms=1000.0).measure()
    slow = _engine(_healthy(exchange_lag_ms=800.0), max_exchange_lag_ms=1000.0).measure()
    assert fast.signals["exchange_lag"] > slow.signals["exchange_lag"] > 0.0


def test_the_risk_multiplier_never_reaches_zero() -> None:
    # Apagar por una métrica de calidad convertiría un problema de datos en una
    # parada total, y esas las decide el kill switch, que tiene auditoría propia.
    report = _engine(
        _healthy(messages=1000, rejected=1000, symbols_with_data=0, books_synced=0),
        risk_floor=0.3,
    ).measure()
    assert report.risk_multiplier >= 0.3
    assert report.risk_multiplier < 1.0


def test_the_reduction_is_proportional_not_binary() -> None:
    mild = _engine(_healthy(rejected=3_000)).measure()
    severe = _engine(_healthy(rejected=8_000)).measure()
    assert mild.risk_multiplier > severe.risk_multiplier


def test_without_any_observable_signal_risk_is_left_alone() -> None:
    # Reducir riesgo aquí sería castigar por no haber medido: este motor no
    # puede convertir su propio silencio en una decisión operativa.
    engine = _engine(DataQualityInputs(), weights={"clock_drift": 0.0})
    report = engine.measure()
    assert report.risk_multiplier == 1.0


def test_the_reasons_name_the_signals_that_dragged_the_score() -> None:
    report = _engine(_healthy(rejected=9_000)).measure()
    assert any("tick_quality" in reason for reason in report.reasons)


def test_the_multiplier_is_one_until_something_has_been_measured() -> None:
    assert _engine(_healthy()).risk_multiplier() == 1.0

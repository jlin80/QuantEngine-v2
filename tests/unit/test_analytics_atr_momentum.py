"""ATR avanzado y momentum: valores calculados a mano y propiedades."""

import pytest
from app.analytics.indicators import (
    acceleration,
    adaptive_atr,
    atr,
    atr_series,
    atr_slope,
    detect_impulse,
    expansion_ratio,
    momentum_score,
    rate_of_change,
    true_ranges,
)

from tests.unit.quant_helpers import make_candles


def test_true_ranges_and_classic_atr():
    candles = make_candles([100.0] * 20, range_pad=0.5)  # rango constante 1.0
    ranges = true_ranges(candles)
    assert len(ranges) == 19
    assert all(r == pytest.approx(1.0) for r in ranges)
    assert atr(candles, 14) == pytest.approx(1.0)
    assert atr(candles[:1]) is None


def test_atr_series_rolling():
    candles = make_candles([100.0] * 30, range_pad=0.5)
    series = atr_series(candles, 14)
    assert len(series) == 29 - 14 + 1
    assert all(v == pytest.approx(1.0) for v in series)


def test_adaptive_atr_follows_recent_ranges():
    quiet = make_candles([100.0] * 30, range_pad=0.1)
    wild_tail = make_candles(
        [100.0] * 30, highs=[100.1] * 20 + [101.0] * 10, lows=[99.9] * 20 + [99.0] * 10
    )
    calm = adaptive_atr(quiet, 14)
    loud = adaptive_atr(wild_tail, 14)
    assert calm is not None and loud is not None
    assert loud > calm


def test_atr_slope_and_expansion():
    expanding = make_candles(
        [100.0] * 40, highs=[100.1] * 30 + [100.8] * 10, lows=[99.9] * 30 + [99.2] * 10
    )
    slope = atr_slope(expanding, 14, 5)
    ratio = expansion_ratio(expanding, 5, 20)
    assert slope is not None and slope > 0
    assert ratio is not None and ratio > 1.0

    compressing = make_candles(
        [100.0] * 40, highs=[100.8] * 30 + [100.05] * 10, lows=[99.2] * 30 + [99.95] * 10
    )
    ratio_c = expansion_ratio(compressing, 5, 20)
    assert ratio_c is not None and ratio_c < 1.0


def test_rate_of_change_and_momentum_score():
    rising = make_candles([100.0 + i * 0.5 for i in range(40)])
    roc = rate_of_change(rising, 10)
    assert roc is not None
    expected = (rising[-1].close / rising[-11].close - 1.0) * 100.0
    assert roc == pytest.approx(expected)

    score = momentum_score(rising, 10)
    assert score is not None and 0 < score <= 100.0
    falling = make_candles([120.0 - i * 0.5 for i in range(40)])
    score_down = momentum_score(falling, 10)
    assert score_down is not None and score_down < 0


def test_acceleration_sign():
    # Subida lenta que se convierte en subida rápida: ROC creciente.
    slow = [100.0 + i * 0.2 for i in range(1, 21)]  # +0.2/bar
    fast = [slow[-1] + i * 2.0 for i in range(1, 11)]  # +2.0/bar
    closes = [100.0] * 10 + slow + fast
    accel = acceleration(make_candles(closes), 10)
    assert accel is not None and accel > 0


def test_detect_impulse():
    flat = [100.0] * 30
    impulse_up = [*flat, 102.0]  # cuerpo 2.0 con ATR ~0.4
    candles = make_candles(impulse_up, opens=[*flat, 100.0])
    impulse = detect_impulse(candles, min_atr_multiple=1.5)
    assert impulse is not None
    assert impulse.direction == "up"
    assert impulse.magnitude > 1.5
    assert impulse.body_ratio > 0.6

    no_impulse = detect_impulse(make_candles(flat), min_atr_multiple=1.5)
    assert no_impulse is None


def test_atr_momentum_determinism():
    closes = [100.0 + (i % 5) * 0.7 for i in range(60)]
    candles = make_candles(closes)
    assert atr(candles) == atr(candles)
    assert momentum_score(candles, 10) == momentum_score(candles, 10)

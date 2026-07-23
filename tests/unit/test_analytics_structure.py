"""Market Structure: swings, etiquetas, tendencia, consolidación y rupturas."""

import pytest
from app.analytics.indicators import analyze_structure, detect_breakout, swing_points
from app.analytics.indicators.structure import fake_breakouts

from tests.unit.quant_helpers import make_candles


def _zigzag_up() -> list[float]:
    """Estructura alcista: HH y HL sucesivos."""
    closes: list[float] = []
    base = 100.0
    for _leg in range(4):
        closes += [base + i for i in range(5)]  # impulso
        base += 4
        closes += [base - i * 0.4 for i in range(1, 4)]  # retroceso parcial
        base = closes[-1] + 0.4 * 3
    return closes


def test_swing_points_and_labels():
    candles = make_candles(_zigzag_up(), range_pad=0.05)
    swings = swing_points(candles, 2, 2)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    assert len(highs) >= 2 and len(lows) >= 2
    labeled_highs = [s.label for s in highs if s.label]
    labeled_lows = [s.label for s in lows if s.label]
    assert all(label == "HH" for label in labeled_highs), "cada máximo supera al previo"
    assert all(label == "HL" for label in labeled_lows), "cada mínimo respeta al previo"


def test_trend_classification():
    up = analyze_structure(make_candles(_zigzag_up(), range_pad=0.05), swing_left=2, swing_right=2)
    assert up is not None and up.trend == "up"

    down_closes = [200.0 - c + 100.0 for c in _zigzag_up()]  # espejo bajista
    down = analyze_structure(make_candles(down_closes, range_pad=0.05), swing_left=2, swing_right=2)
    assert down is not None and down.trend == "down"

    flat = analyze_structure(make_candles([100.0, 101.0] * 25))
    assert flat is not None and flat.trend == "sideways"


def test_consolidation_and_accumulation_bias():
    # Rango estrecho con cierres cerca del máximo (CLV positivo) y volumen.
    closes = [100.0 + (i % 2) * 0.2 for i in range(40)]
    highs = [c + 0.05 for c in closes]
    lows = [c - 0.35 for c in closes]
    candles = make_candles(closes, highs=highs, lows=lows)
    result = analyze_structure(candles, consolidation_band_pct=1.0)
    assert result is not None
    assert result.consolidation is True
    assert result.bias == "accumulation"


def test_breakout_validation_by_volume():
    base = [100.0] * 30
    candles_ok = make_candles([*base, 101.5], volumes=[10.0] * 30 + [30.0])
    breakout = detect_breakout(candles_ok, lookback=20, min_volume_ratio=1.2)
    assert breakout is not None
    assert breakout.direction == "up"
    assert breakout.valid is True
    assert breakout.volume_ratio == pytest.approx(3.0)

    candles_weak = make_candles([*base, 101.5], volumes=[10.0] * 30 + [5.0])
    weak = detect_breakout(candles_weak, lookback=20, min_volume_ratio=1.2)
    assert weak is not None and weak.valid is False

    inside = detect_breakout(make_candles(base), lookback=20)
    assert inside is None


def test_fake_breakout_detection():
    # Mecha por encima del rango con cierre de vuelta adentro.
    closes = [100.0] * 29 + [100.0]
    highs = [100.2] * 29 + [101.5]
    lows = [99.8] * 30
    candles = make_candles(closes, highs=highs, lows=lows)
    fakes = fake_breakouts(candles, lookback=20, scan=3)
    assert "up" in fakes


def test_structure_determinism():
    candles = make_candles(_zigzag_up(), range_pad=0.05)
    assert analyze_structure(candles) == analyze_structure(candles)

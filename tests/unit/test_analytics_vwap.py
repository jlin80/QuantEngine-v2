"""VWAP: sesión, anclado, bandas σ, pendiente, distancia y determinismo."""

from datetime import UTC, datetime

import pytest
from app.analytics.indicators import (
    anchor_index_at,
    anchored_vwap,
    session_anchor,
    vwap_bands,
)

from tests.unit.quant_helpers import TS, make_candles


def test_session_anchor_day_week_month():
    moment = datetime(2026, 7, 15, 14, 30, tzinfo=UTC)  # miércoles
    assert session_anchor("day", moment) == datetime(2026, 7, 15, tzinfo=UTC)
    assert session_anchor("week", moment) == datetime(2026, 7, 13, tzinfo=UTC)  # lunes
    assert session_anchor("month", moment) == datetime(2026, 7, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="Unknown session kind"):
        session_anchor("quarter", moment)


def test_anchor_index_at():
    candles = make_candles([100.0] * 10)
    assert anchor_index_at(candles, TS) == 0
    assert anchor_index_at(candles, candles[4].start) == 4
    assert anchor_index_at(candles, candles[-1].end) is None


def test_anchored_vwap_hand_computed():
    # vwaps por vela = precio típico; volúmenes desiguales.
    candles = make_candles([100.0, 110.0], volumes=[10.0, 30.0], vwaps=[100.0, 110.0])
    value = anchored_vwap(candles, 0)
    assert value == pytest.approx((100.0 * 10 + 110.0 * 30) / 40.0)
    assert anchored_vwap(candles, 1) == pytest.approx(110.0)
    assert anchored_vwap(candles, 5) is None  # índice fuera de rango


def test_vwap_bands_geometry_and_distance():
    closes = [100.0, 102.0, 98.0, 101.0, 99.0] * 6
    candles = make_candles(closes, vwaps=closes)
    bands = vwap_bands(candles, 0, reference_price=closes[-1])
    assert bands is not None
    assert bands.std > 0
    assert bands.lower_2 < bands.lower_1 < bands.value < bands.upper_1 < bands.upper_2
    assert bands.upper_1 - bands.value == pytest.approx(bands.std)
    expected_distance = (closes[-1] - bands.value) / bands.value * 100.0
    assert bands.distance_pct == pytest.approx(expected_distance)
    # deviation invierte la geometría de las bandas.
    assert bands.deviation(bands.upper_1) == pytest.approx(1.0)
    assert bands.deviation(bands.lower_2) == pytest.approx(-2.0)


def test_vwap_bands_slope_sign():
    rising = make_candles([100.0 + i for i in range(30)], vwaps=[100.0 + i for i in range(30)])
    falling = make_candles([130.0 - i for i in range(30)], vwaps=[130.0 - i for i in range(30)])
    up = vwap_bands(rising, 0)
    down = vwap_bands(falling, 0)
    assert up is not None and up.slope_pct_per_bar > 0
    assert down is not None and down.slope_pct_per_bar < 0


def test_vwap_no_volume_returns_none():
    candles = make_candles([100.0] * 10, volumes=[0.0] * 10)
    assert anchored_vwap(candles, 0) is None
    assert vwap_bands(candles, 0) is None


def test_vwap_determinism():
    closes = [100.0 + (i % 7) * 0.3 for i in range(50)]
    candles = make_candles(closes)
    first = vwap_bands(candles, 5)
    second = vwap_bands(candles, 5)
    assert first == second

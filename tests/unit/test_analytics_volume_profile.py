"""Volume Profile: POC, value area, nodos y determinismo."""

import pytest
from app.analytics.indicators import volume_profile

from tests.unit.quant_helpers import make_candles


def _clustered_candles() -> list:
    """Volumen concentrado alrededor de 100; colas finas en 90 y 110."""
    closes = [90.0] * 2 + [100.0] * 16 + [110.0] * 2
    volumes = [5.0] * 2 + [50.0] * 16 + [5.0] * 2
    return make_candles(closes, volumes=volumes, range_pad=1.0)


def test_poc_sits_on_the_heavy_cluster():
    profile = volume_profile(_clustered_candles(), bins=20)
    assert profile is not None
    assert 97.0 <= profile.poc <= 103.0, "el POC debe caer en el cluster de 100"
    assert profile.total_volume == pytest.approx(5 * 4 + 50 * 16)


def test_value_area_contains_target_volume():
    profile = volume_profile(_clustered_candles(), bins=20, value_area_pct=0.70)
    assert profile is not None
    assert profile.value_area_pct >= 0.70
    assert profile.val <= profile.poc <= profile.vah
    assert profile.position(profile.poc) == "inside_value"
    assert profile.position(115.0) == "above_value"
    assert profile.position(85.0) == "below_value"


def test_hvn_and_lvn_detection():
    profile = volume_profile(_clustered_candles(), bins=20, node_ratio=1.5)
    assert profile is not None
    assert any(97.0 <= n <= 103.0 for n in profile.hvn), "HVN en el cluster"
    assert profile.lvn, "las zonas vacías entre clusters generan LVN"


def test_degenerate_inputs_return_none():
    assert volume_profile([], bins=20) is None
    flat_zero = make_candles([100.0] * 10, volumes=[0.0] * 10)
    assert volume_profile(flat_zero, bins=20) is None
    assert volume_profile(make_candles([100.0] * 5), bins=1) is None


def test_profile_determinism():
    candles = _clustered_candles()
    first = volume_profile(candles, bins=24)
    second = volume_profile(candles, bins=24)
    assert first == second

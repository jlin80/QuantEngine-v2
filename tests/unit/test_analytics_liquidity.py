"""Liquidity Engine: pools, stop hunts, rechazo y continuación."""

from app.analytics.indicators import analyze_liquidity

from tests.unit.quant_helpers import make_candles


def _range_with_sweep(*, reclaim: bool) -> list:
    """Rango 100-104 con barrido final del piso (con o sin reclamo)."""
    closes = [100.0, 104.0, 100.0, 104.0, 100.0, 104.0, 100.5]
    highs = [c + 0.2 for c in closes]
    lows = [99.8, 103.8, 99.8, 103.8, 99.8, 103.8, 99.0]  # mecha final bajo 99.8
    if not reclaim:
        closes[-1] = 99.2  # cierra por debajo del pool: continuación
        highs[-1] = 100.0
    return make_candles(closes, highs=highs, lows=lows)


def test_pools_include_equal_levels_and_range_extremes():
    liquidity = analyze_liquidity(make_candles([100.0, 104.0] * 5), swing_left=1, swing_right=1)
    assert liquidity is not None
    kinds = {p.kind for p in liquidity.pools}
    assert kinds == {"high", "low"}
    assert any(not p.internal for p in liquidity.pools), "los extremos son liquidez externa"


def test_stop_hunt_with_rejection():
    liquidity = analyze_liquidity(
        _range_with_sweep(reclaim=True), swing_left=1, swing_right=1, hunt_scan=2
    )
    assert liquidity is not None
    rejections = [h for h in liquidity.hunts if h.rejection and h.pool.kind == "low"]
    assert rejections, "mecha bajo el pool + cierre encima = stop hunt con rechazo"
    assert rejections[-1].continuation is False
    assert rejections[-1].wick_ratio > 0.4


def test_sweep_continuation_is_not_rejection():
    liquidity = analyze_liquidity(
        _range_with_sweep(reclaim=False), swing_left=1, swing_right=1, hunt_scan=2
    )
    assert liquidity is not None
    lows = [h for h in liquidity.hunts if h.pool.kind == "low"]
    assert lows, "el pool bajo fue barrido"
    assert lows[-1].continuation is True, "cierre más allá del pool = ruptura genuina"
    assert lows[-1].rejection is False


def test_nearest_pool_lookup():
    liquidity = analyze_liquidity(make_candles([100.0, 104.0] * 5), swing_left=1, swing_right=1)
    assert liquidity is not None
    above = liquidity.nearest_pool(102.0, "high")
    below = liquidity.nearest_pool(102.0, "low")
    assert above is not None and above.level > 102.0
    assert below is not None and below.level < 102.0


def test_insufficient_data_returns_none():
    assert analyze_liquidity(make_candles([100.0] * 3)) is None

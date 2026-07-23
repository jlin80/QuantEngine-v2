"""Smart Money Concepts: FVG, order blocks, sweeps, EQH/EQL, BOS/CHOCH/MSS."""

import pytest
from app.analytics.indicators import analyze_smc
from app.analytics.indicators.smc import (
    equal_levels,
    fair_value_gaps,
    liquidity_sweeps,
    order_blocks,
    premium_discount,
    structure_breaks,
)
from app.analytics.indicators.structure import swing_points

from tests.unit.quant_helpers import make_candles


def test_bullish_fvg_detection_and_fill_tracking():
    # Vela central desplazada: el low de la 3ª queda sobre el high de la 1ª.
    closes = [100.0] * 14 + [100.0, 102.0, 103.0, 103.0, 103.0]
    highs = [100.2] * 14 + [100.3, 102.5, 103.3, 103.2, 103.2]
    lows = [99.8] * 14 + [99.8, 100.9, 102.6, 102.8, 102.9]
    candles = make_candles(closes, highs=highs, lows=lows)
    gaps = fair_value_gaps(candles, min_size_atr=0.3)
    bullish = [g for g in gaps if g.direction == "bullish"]
    assert bullish, "debe detectar el gap alcista"
    gap = max(bullish, key=lambda g: g.top - g.bottom)  # el gap principal
    assert gap.bottom == pytest.approx(100.3)  # high de la vela previa
    assert gap.top == pytest.approx(102.6)  # low de la vela posterior
    assert gap.filled_pct == 0.0, "nada volvió a entrar al gap"


def test_fvg_fully_filled_is_dropped():
    closes = [100.0] * 14 + [100.0, 102.0, 103.0, 99.5]  # la última rellena todo
    highs = [100.2] * 14 + [100.3, 102.5, 103.3, 103.0]
    lows = [99.8] * 14 + [99.8, 100.9, 102.6, 99.4]
    gaps = fair_value_gaps(make_candles(closes, highs=highs, lows=lows), min_size_atr=0.3)
    assert not [g for g in gaps if g.direction == "bullish"]


def test_order_block_detection_and_states():
    # Vela bajista seguida de desplazamiento alcista fuerte.
    closes = [100.0] * 14 + [99.5, 102.5, 103.0, 103.2]
    opens = [100.0] * 14 + [100.2, 99.6, 102.6, 103.0]
    candles = make_candles(closes, opens=opens, range_pad=0.2)
    blocks = order_blocks(candles, displacement_atr=1.5)
    bullish = [b for b in blocks if b.direction == "bullish"]
    assert bullish, "la vela bajista previa al impulso es un OB de demanda"
    block = bullish[-1]
    assert block.top == pytest.approx(100.2)  # cuerpo de la vela contraria
    assert block.bottom == pytest.approx(99.5)
    assert block.mitigated is False
    assert block.breaker is False


def test_liquidity_sweep_reclaimed():
    # Swing high en 105; luego mecha que lo supera y cierra debajo.
    closes = [100.0, 102.0, 105.0, 102.0, 100.0, 100.0, 100.0, 100.0, 104.0]
    highs = [100.2, 102.2, 105.2, 102.2, 100.2, 100.2, 100.2, 100.2, 106.0]
    lows = [99.8, 101.8, 104.8, 101.8, 99.8, 99.8, 99.8, 99.8, 99.9]
    candles = make_candles(closes, highs=highs, lows=lows)
    swings = swing_points(candles, 2, 2)
    sweeps = liquidity_sweeps(candles, swings, scan=3, tolerance_pct=0.02)
    highs_swept = [s for s in sweeps if s.kind == "high"]
    assert highs_swept, "la mecha final barre el swing high"
    assert highs_swept[-1].reclaimed is True


def test_equal_levels_clustering():
    swings = swing_points(
        make_candles(
            [100.0, 104.0, 100.0, 104.02, 100.0, 104.01, 100.0, 98.0, 100.0],
            range_pad=0.05,
        ),
        1,
        1,
    )
    levels = equal_levels(swings, tolerance_pct=0.05)
    eq_highs = [lvl for lvl in levels if lvl.kind == "high"]
    assert eq_highs and eq_highs[0].count >= 2, "tres máximos casi iguales forman un pool"


def test_structure_breaks_bos_choch_mss():
    # Zigzag alcista con swings confirmados; colapso final con cuerpo enorme.
    closes = [
        100.0,
        102.0,
        104.0,
        102.5,
        101.0,  # swing high (104) + swing low (101)
        103.0,
        105.0,
        107.0,
        105.5,
        104.0,  # BOS up, nuevo high/low
        106.0,
        108.0,
        110.0,
        97.0,  # colapso: rompe el último swing low contra tendencia alcista
    ]
    opens = [*closes[:-1], 105.0]  # solo la vela final tiene cuerpo real
    candles = make_candles(closes, opens=opens)
    swings = swing_points(candles, 2, 2)
    breaks = structure_breaks(candles, swings, mss_displacement_atr=1.5)
    assert breaks, "debe detectar rupturas"
    assert any(b.direction == "up" and b.kind == "BOS" for b in breaks)
    last = breaks[-1]
    assert last.direction == "down"
    assert last.kind == "CHOCH", "la ruptura contra la tendencia alcista es CHOCH"
    assert last.mss is True, "el desplazamiento enorme la convierte en MSS"


def test_premium_discount_zones():
    candles = make_candles([100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 109.0], range_pad=0.3)
    swings = swing_points(candles, 1, 1)
    zone = premium_discount(candles, swings)
    assert zone is not None
    assert zone.zone == "premium", "cierre 109 en la parte alta del rango"
    assert zone.position > 0.5
    assert zone.range_low < zone.equilibrium < zone.range_high


def test_analyze_smc_composite_and_determinism():
    closes = [100.0 + (i % 9) * 0.8 for i in range(80)]
    candles = make_candles(closes)
    first = analyze_smc(candles)
    second = analyze_smc(candles)
    assert first is not None
    assert first == second, "el análisis SMC es determinista"
    assert first.atr > 0
    assert first.swings, "debe encontrar pivotes"
    assert analyze_smc(candles[:5]) is None, "datos insuficientes"

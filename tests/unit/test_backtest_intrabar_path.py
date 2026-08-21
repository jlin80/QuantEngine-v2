"""Camino intrabar del backtest: los stops se rellenan en su nivel.

Hasta el 2026-08-21 el recorrido publicaba solo ``open/low/high/close``, así que
el único precio por debajo de la entrada era el mínimo de la vela y una salida
por stop se materializaba ahí. Medido sobre XAUUSDM, eso costaba -1,202R por
stop contra -0,975R reales: se cobraba la excursión completa de la vela en vez
del nivel arriesgado.
"""

from app.backtesting.engine.engine import _exit_levels, _ohlc_path

from tests.unit.quant_helpers import make_candles


def _candle(open_: float, high: float, low: float, close: float):
    """One candle with the given OHLC."""
    return make_candles([close], symbol="XAUUSDM", opens=[open_], highs=[high], lows=[low])[0]


def test_without_levels_the_path_is_the_classic_ohlc():
    bullish = _candle(100.0, 110.0, 90.0, 105.0)
    assert _ohlc_path(bullish) == (100.0, 90.0, 110.0, 105.0)
    bearish = _candle(100.0, 110.0, 90.0, 95.0)
    assert _ohlc_path(bearish) == (100.0, 110.0, 90.0, 95.0)


def test_a_stop_inside_the_candle_is_visited_at_its_own_level():
    # Vela alcista: baja a 90 antes de subir. Un stop en 97 debe publicarse
    # ANTES del minimo, para que la posicion cierre en 97 y no en 90.
    candle = _candle(100.0, 110.0, 90.0, 105.0)
    path = _ohlc_path(candle, [97.0])
    # 97 aparece dos veces a proposito: el precio pasa por ahi bajando y vuelve
    # a pasar subiendo de 90 a 110. La segunda visita es donde se llenaria el
    # objetivo de un corto.
    assert path == (100.0, 97.0, 90.0, 97.0, 110.0, 105.0)
    assert path.index(97.0) < path.index(90.0)


def test_levels_outside_the_candle_are_ignored():
    candle = _candle(100.0, 110.0, 90.0, 105.0)
    # 80 y 120 nunca se tocaron: publicarlos inventaria precio.
    assert _ohlc_path(candle, [80.0, 120.0]) == (100.0, 90.0, 110.0, 105.0)


def test_several_levels_keep_the_travel_order():
    candle = _candle(100.0, 110.0, 90.0, 105.0)
    path = _ohlc_path(candle, [95.0, 98.0, 108.0, 106.0])
    # Bajada 100→90 en orden descendente, subida 90→110 ascendente, y la
    # vuelta 110→105 desciende de nuevo.
    assert path == (100.0, 98.0, 95.0, 90.0, 95.0, 98.0, 106.0, 108.0, 110.0, 108.0, 106.0, 105.0)


def test_the_path_never_leaves_the_candle_range():
    candle = _candle(100.0, 110.0, 90.0, 105.0)
    path = _ohlc_path(candle, [93.0, 107.0])
    assert min(path) == 90.0
    assert max(path) == 110.0


def test_exit_levels_reads_stops_and_targets_and_skips_the_empty_ones():
    class _Position:
        def __init__(self, stop, target):
            self.stop_loss = stop
            self.take_profit = target

    levels = _exit_levels([_Position(97.0, 108.0), _Position(0.0, None)])
    assert sorted(levels) == [97.0, 108.0]

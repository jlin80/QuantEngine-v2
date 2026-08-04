"""Las velas de marco superior no pueden mirar al futuro.

El motor es hoy monotimeframe: todo 1m, y con el `lookback` de 50 del detector de
regimen, 50 minutos de vision total. Para medir si un contexto superior aporta
algo hace falta construirlo desde la misma serie de 1m — y ahi el riesgo no es
un backtest optimista, es uno **invalido**: publicar la vela de 1h en curso le
daria a la estrategia el maximo y el minimo de minutos que todavia no han
ocurrido.

Estos tests fijan exactamente eso.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.backtesting.htf import HigherTimeframeAggregator, bucket_start
from app.market.models import Candle, Timeframe

_BASE = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _m1(minute: int, *, high: float, low: float, close: float | None = None) -> Candle:
    start = _BASE + timedelta(minutes=minute)
    return Candle(
        symbol="BTCUSDT",
        provider="test",
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=100.0,
        high=high,
        low=low,
        close=100.0 if close is None else close,
        volume=1.0,
        trades=1,
        buy_volume=0.6,
        sell_volume=0.4,
        closed=True,
        source="test",
    )


def _agg(tf: Timeframe = Timeframe.M15) -> HigherTimeframeAggregator:
    return HigherTimeframeAggregator(timeframe=tf, symbol="BTCUSDT")


# ----------------------------------------------------------------------
# Lo que impide que el backtest sea invalido
# ----------------------------------------------------------------------


def test_a_bucket_in_progress_is_never_published():
    """Mientras el bucket sigue abierto no sale ninguna vela.

    Es la propiedad que hace valido el backtest: la estrategia no puede ver el
    rango de una vela de 15m hasta que sus 15 minutos hayan pasado.
    """
    aggregator = _agg()

    emitted = [aggregator.push(_m1(i, high=101.0, low=99.0)) for i in range(15)]

    assert emitted == [None] * 15


def test_the_candle_is_published_only_when_the_next_bucket_opens():
    aggregator = _agg()
    for i in range(15):
        aggregator.push(_m1(i, high=101.0, low=99.0))

    closed = aggregator.push(_m1(15, high=101.0, low=99.0))

    assert closed is not None
    assert closed.timeframe is Timeframe.M15
    assert closed.start == _BASE
    assert closed.end == _BASE + timedelta(minutes=15)


def test_a_published_candle_never_contains_future_extremes():
    """El caso que haria invalido el backtest, con numeros.

    El minuto 20 tiene un maximo de 500. La vela de 15m que se publica al
    entrar en el bucket siguiente cubre los minutos 0-14 y **no puede** incluir
    ese 500: pertenece a un bucket posterior.
    """
    aggregator = _agg()
    for i in range(15):
        aggregator.push(_m1(i, high=101.0, low=99.0))
    closed = aggregator.push(_m1(15, high=500.0, low=99.0))

    assert closed is not None
    assert closed.high == 101.0
    assert closed.high < 500.0


# ----------------------------------------------------------------------
# Que la vela agregada sea correcta
# ----------------------------------------------------------------------


def test_the_aggregate_takes_the_extremes_of_its_minutes():
    aggregator = _agg()
    aggregator.push(_m1(0, high=105.0, low=99.0))
    for i in range(1, 14):
        aggregator.push(_m1(i, high=101.0, low=97.0))
    aggregator.push(_m1(14, high=102.0, low=95.0, close=103.0))

    closed = aggregator.push(_m1(15, high=101.0, low=99.0))

    assert closed is not None
    assert closed.high == 105.0
    assert closed.low == 95.0
    assert closed.close == 103.0  # cierre del ULTIMO minuto del bucket
    assert closed.open == 100.0  # apertura del PRIMERO


def test_volume_and_flow_are_summed():
    aggregator = _agg()
    for i in range(15):
        aggregator.push(_m1(i, high=101.0, low=99.0))

    closed = aggregator.push(_m1(15, high=101.0, low=99.0))

    assert closed is not None
    assert closed.volume == pytest.approx(15.0)
    assert closed.buy_volume == pytest.approx(9.0)
    assert closed.sell_volume == pytest.approx(6.0)


# ----------------------------------------------------------------------
# Alineacion
# ----------------------------------------------------------------------


def test_buckets_are_aligned_to_the_epoch_not_to_the_first_candle():
    """Alineado al epoch, como los exchanges: reproducible entre corridas.

    Si el bucket empezara en la primera vela recibida, dos corridas con
    ventanas distintas darian velas de 15m diferentes sobre el mismo mercado.
    """
    odd_start = datetime(2026, 8, 1, 10, 7, tzinfo=UTC)

    assert bucket_start(odd_start, 900) == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    assert bucket_start(odd_start, 3600) == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_an_hourly_aggregate_needs_sixty_minutes():
    aggregator = _agg(Timeframe.H1)

    emitted = [aggregator.push(_m1(i, high=101.0, low=99.0)) for i in range(60)]
    closed = aggregator.push(_m1(60, high=101.0, low=99.0))

    assert emitted == [None] * 60
    assert closed is not None
    assert closed.end - closed.start == timedelta(hours=1)


def test_reset_forgets_the_bucket_in_progress():
    aggregator = _agg()
    for i in range(10):
        aggregator.push(_m1(i, high=101.0, low=99.0))

    aggregator.reset()

    # Tras el reset, el bucket arranca de cero: la vela 10 no cierra nada.
    assert aggregator.push(_m1(10, high=101.0, low=99.0)) is None


def test_a_timeframe_without_fixed_duration_is_rejected():
    """`tick` y `1M` no tienen duracion fija: fallar es mejor que inventarla."""
    aggregator = _agg(Timeframe.TICK)

    with pytest.raises(ValueError, match="duración fija"):
        aggregator.push(_m1(0, high=101.0, low=99.0))

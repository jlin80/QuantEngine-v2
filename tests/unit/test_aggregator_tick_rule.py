"""Regla del tick: las velas desde quotes clasifican agresor.

Sin esto, ``add_ticker`` doblaba con ``side=None`` y toda vela construida desde
quotes salia con ``buy_volume`` y ``sell_volume`` en cero. Las estrategias
``delta_confirmation`` y ``cvd`` calculaban siempre 0 y aun asi figuraban
activas y votaban en el consenso. Verificado en produccion el 2026-08-21: el
CFD de Exness no publica operaciones, asi que los quotes son lo unico que hay.
"""

from app.market.aggregator import CandleAggregator
from app.market.models import Timeframe

from tests.unit.quant_helpers import make_ticker


def _aggregator() -> CandleAggregator:
    return CandleAggregator([Timeframe.M1])


def _feed(aggregator: CandleAggregator, prices: list[float]) -> None:
    for price in prices:
        half = 0.005
        aggregator.add_ticker(make_ticker(bid=price - half, ask=price + half))


def test_a_rising_quote_counts_as_buyer_and_a_falling_one_as_seller():
    aggregator = _aggregator()
    _feed(aggregator, [100.0, 100.1, 100.2, 100.1])
    candle = aggregator.building_candle("BTCUSDT", Timeframe.M1)

    assert candle is not None
    # El primero no tiene precio previo: no se le atribuye lado.
    assert candle.buy_volume == 2.0
    assert candle.sell_volume == 1.0


def test_an_unchanged_quote_inherits_the_previous_side():
    aggregator = _aggregator()
    _feed(aggregator, [100.0, 100.1, 100.1, 100.1])
    candle = aggregator.building_candle("BTCUSDT", Timeframe.M1)

    assert candle is not None
    # Una subida y dos quotes planos que heredan el lado comprador.
    assert candle.buy_volume == 3.0
    assert candle.sell_volume == 0.0


def test_the_first_quote_of_a_symbol_has_no_side():
    """Suponer un lado sin precio previo inventaria delta en cada arranque."""
    aggregator = _aggregator()
    _feed(aggregator, [100.0])
    candle = aggregator.building_candle("BTCUSDT", Timeframe.M1)

    assert candle is not None
    assert candle.volume == 1.0
    assert candle.buy_volume == 0.0
    assert candle.sell_volume == 0.0


def test_delta_is_no_longer_always_zero():
    """La prueba de fondo: el motivo por el que delta y cvd no median nada."""
    aggregator = _aggregator()
    _feed(aggregator, [100.0, 100.1, 100.2, 100.3, 100.4])
    candle = aggregator.building_candle("BTCUSDT", Timeframe.M1)

    assert candle is not None
    assert candle.buy_volume - candle.sell_volume != 0.0


def test_the_tick_rule_state_is_per_symbol():
    aggregator = _aggregator()
    aggregator.add_ticker(make_ticker(symbol="AAA", bid=99.99, ask=100.01))
    aggregator.add_ticker(make_ticker(symbol="BBB", bid=199.99, ask=200.01))
    # El segundo simbolo no debe heredar el precio previo del primero.
    aggregator.add_ticker(make_ticker(symbol="BBB", bid=200.09, ask=200.11))

    candle = aggregator.building_candle("BBB", Timeframe.M1)
    assert candle is not None
    assert candle.buy_volume == 1.0
    assert candle.sell_volume == 0.0

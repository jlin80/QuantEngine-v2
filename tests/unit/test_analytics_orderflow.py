"""Order Flow: delta, CVD, agresión, absorción, agotamiento y heurísticas."""

import pytest
from app.analytics.indicators import analyze_order_flow
from app.market.models import TradeSide

from tests.unit.quant_helpers import make_book, make_trade


def _trades(pattern: list[tuple[TradeSide, float, float]]) -> list:
    """Trades (lado, tamaño, precio) en orden cronológico."""
    return [make_trade(price=price, size=size, side=side) for side, size, price in pattern]


def test_delta_and_aggression():
    trades = _trades([(TradeSide.BUY, 2.0, 100.0)] * 9 + [(TradeSide.SELL, 1.0, 100.1)] * 3)
    flow = analyze_order_flow(trades, buckets=4)
    assert flow is not None
    assert flow.buy_volume == pytest.approx(18.0)
    assert flow.sell_volume == pytest.approx(3.0)
    assert flow.delta == pytest.approx(15.0)
    assert flow.aggression_ratio == pytest.approx(18.0 / 21.0)


def test_cvd_series_and_slope():
    # Primera mitad neutra, segunda mitad compradora → CVD acelerando.
    neutral = [(TradeSide.BUY, 1.0, 100.0), (TradeSide.SELL, 1.0, 100.0)] * 9
    buyers = [(TradeSide.BUY, 2.0, 100.0)] * 18
    flow = analyze_order_flow(_trades(neutral + buyers), buckets=6)
    assert flow is not None
    assert len(flow.cvd_series) == 6
    assert flow.cvd_series[-1] > flow.cvd_series[0]
    assert flow.cvd_slope > 0.3


def test_absorption_heavy_buying_no_progress():
    # Compra agresiva dominante con precio clavado → la oferta absorbe.
    trades = _trades([(TradeSide.BUY, 3.0, 100.0)] * 20 + [(TradeSide.SELL, 1.0, 100.0)] * 4)
    flow = analyze_order_flow(trades, buckets=4, absorption_move_pct=0.05)
    assert flow is not None
    assert flow.absorption == "bearish"
    assert flow.price_change_pct == pytest.approx(0.0)


def test_exhaustion_of_buyers():
    # Delta comprador decreciente bucket a bucket.
    strong = [(TradeSide.BUY, 5.0, 100.0)] * 5
    medium = [(TradeSide.BUY, 2.0, 100.2)] * 5
    weak = [(TradeSide.BUY, 0.5, 100.3)] * 5
    flow = analyze_order_flow(_trades(strong + medium + weak), buckets=3)
    assert flow is not None
    assert flow.exhaustion == "buyers"


def test_book_metrics_and_experimental_scores():
    trades = _trades([(TradeSide.BUY, 1.0, 100.0)] * 12)
    # Libro cargado del lado bid + niveles lejanos enormes (spoof-like).
    book = make_book(
        bids=[(100.0, 8.0), (99.9, 6.0), (99.8, 6.0)] + [(99.0 - i, 50.0) for i in range(10)],
        asks=[(100.1, 2.0), (100.2, 1.0)],
    )
    flow = analyze_order_flow(trades, book, buckets=3)
    assert flow is not None
    assert flow.imbalance is not None and flow.imbalance > 0
    assert flow.book_pressure is not None
    assert 0.0 <= flow.spoofing_score <= 1.0
    assert flow.spoofing_score > 0, "liquidez lejana desproporcionada puntúa"
    assert flow.liquidity_consumption is not None and flow.liquidity_consumption > 0


def test_iceberg_score_on_repeated_price_volume():
    # 60 de volumen ejecutado en un precio con top of book visible de 3.
    trades = _trades([(TradeSide.BUY, 5.0, 100.0)] * 12)
    book = make_book(bids=[(100.0, 2.0)], asks=[(100.1, 1.0)])
    flow = analyze_order_flow(trades, book, buckets=3, iceberg_multiple=3.0)
    assert flow is not None
    assert flow.iceberg_score > 0


def test_too_few_trades_returns_none():
    assert analyze_order_flow(_trades([(TradeSide.BUY, 1.0, 100.0)] * 3), buckets=6) is None


def test_orderflow_determinism():
    trades = _trades([(TradeSide.BUY, 1.5, 100.0), (TradeSide.SELL, 1.0, 100.1)] * 15)
    assert analyze_order_flow(trades, buckets=5) == analyze_order_flow(trades, buckets=5)

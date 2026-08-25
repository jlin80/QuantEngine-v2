"""Sin tape, el flujo se deduce del volumen firmado de las velas.

MT5 nunca emite `Trade`: su polling solo publica `Ticker`. La feature
`orderflow` leia `get_recent_trades()` y salia vacia, asi que
`delta_confirmation` y `cvd` no emitian una sola senal — verificado en
produccion el 2026-08-25, cuatro dias despues de poblar el volumen firmado de
las velas con la regla del tick. Poblar la vela era necesario pero no
suficiente: los indicadores leen de la feature, no de la vela.
"""

from dataclasses import replace

from app.engine.feature_store import FeatureStore
from app.market.models import TradeSide

from tests.unit.execution_helpers import make_market_with_state
from tests.unit.quant_helpers import make_candles


def _store(candles: list) -> FeatureStore:
    market, _ = make_market_with_state(candles=candles)
    return FeatureStore(market)


def _signed_candles(n: int = 40):
    """Velas con volumen comprador y vendedor, como las produce la regla del tick."""
    candles = make_candles([100.0 + i * 0.1 for i in range(n)], symbol="XAUUSDM")
    signed = []
    for i, candle in enumerate(candles):
        # Asimetrico a proposito: con 60/20 alternado en un numero par de
        # velas el delta suma exactamente cero y el test no distinguiria el
        # fallback funcionando de el fallback ausente.
        buy = 60.0 if i % 3 else 20.0
        sell = 20.0 if i % 3 else 60.0
        signed.append(replace(candle, buy_volume=buy, sell_volume=sell, volume=buy + sell))
    return signed


async def test_delta_falls_back_to_candles_when_there_is_no_tape():
    store = _store(_signed_candles())
    delta = await store.get("delta", "XAUUSDM")

    assert delta is not None
    assert delta != 0.0


async def test_the_orderflow_feature_is_no_longer_empty():
    store = _store(_signed_candles())
    flow = await store.get_object("orderflow", "XAUUSDM")

    assert flow is not None


async def test_candles_without_signed_volume_still_report_absence():
    """La ausencia de medicion no es un cero: sin lado, la feature sigue en None."""
    store = _store(make_candles([100.0 + i * 0.1 for i in range(40)], symbol="XAUUSDM"))
    assert await store.get("delta", "XAUUSDM") is None


async def test_the_synthetic_flow_keeps_the_sign_of_the_candle():
    store = _store(_signed_candles(4))
    trades = store._flow_from_candles("XAUUSDM", {})

    assert trades
    buys = sum(t.size for t in trades if t.side is TradeSide.BUY)
    sells = sum(t.size for t in trades if t.side is TradeSide.SELL)
    assert buys > 0 and sells > 0

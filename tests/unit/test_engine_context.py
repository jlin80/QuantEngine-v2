"""Market Context Engine: sesiones, noticias, volatilidad y calidad de dato."""

from datetime import UTC, datetime, timedelta

from app.config.settings import QuantContextSettings, QuantRegimeSettings
from app.engine.feature_store import FeatureStore
from app.engine.market_context import MarketContextEngine
from app.engine.models import VolatilityState
from app.engine.regime_detection import RegimeDetector
from app.market.services import MarketDataService
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_candles, make_market, make_ticker, make_trade


def _engine(
    market: MarketDataService, settings: QuantContextSettings | None = None
) -> MarketContextEngine:
    features = FeatureStore(market)
    regime = RegimeDetector(market, QuantRegimeSettings())
    return MarketContextEngine(market, features, regime, settings or QuantContextSettings())


def _at_hour(hour: int) -> datetime:
    return datetime(2026, 7, 15, hour, 30, tzinfo=UTC)


def test_active_sessions_default_hours():
    engine = _engine(make_market())
    assert set(engine.active_sessions(_at_hour(8))) == {"asia", "europe"}
    assert engine.active_sessions(_at_hour(12)) == ("europe",)
    assert set(engine.active_sessions(_at_hour(14))) == {"europe", "america"}
    assert engine.active_sessions(_at_hour(23)) == ()


def test_active_sessions_wrap_around_midnight():
    settings = QuantContextSettings(session_hours={"overnight": (22, 4)})
    engine = _engine(make_market(), settings)
    assert engine.active_sessions(_at_hour(23)) == ("overnight",)
    assert engine.active_sessions(_at_hour(2)) == ("overnight",)
    assert engine.active_sessions(_at_hour(12)) == ()


def test_news_blackout_window():
    now = utc_now()
    settings = QuantContextSettings(
        news_blackouts=[
            ((now - timedelta(minutes=1)).isoformat(), (now + timedelta(minutes=1)).isoformat()),
            ("no-es-fecha", "tampoco"),  # entradas inválidas se ignoran
        ]
    )
    engine = _engine(make_market(), settings)
    assert engine.in_news_blackout(now) is True
    assert engine.in_news_blackout(now + timedelta(hours=2)) is False


async def test_volatility_classification_high_and_low():
    # Rango 1.2 sobre precio 100 => ATR% ~1.2% >= 0.8 => HIGH.
    market_high = make_market(
        candles=make_candles([100.0] * 30, range_pad=0.6),
        trades=[make_trade(price=100.0)],
    )
    ctx_high = await _engine(market_high).build("BTCUSDT")
    assert ctx_high.volatility is VolatilityState.HIGH

    # Rango 0.02 sobre precio 100 => ATR% 0.02% <= 0.05 => LOW.
    market_low = make_market(
        candles=make_candles([100.0] * 30, range_pad=0.01),
        trades=[make_trade(price=100.0)],
    )
    ctx_low = await _engine(market_low).build("BTCUSDT")
    assert ctx_low.volatility is VolatilityState.LOW


async def test_spread_elevated_flag():
    market = make_market(
        candles=make_candles([100.0] * 30),
        ticker=make_ticker(bid=100.0, ask=100.2),  # ~20 bps
        trades=[make_trade(price=100.0)],
    )
    context = await _engine(market).build("BTCUSDT")
    assert context.spread_bps is not None and context.spread_bps > 5.0
    assert context.spread_elevated is True


async def test_data_quality_zero_without_data():
    context = await _engine(make_market()).build("GHOSTUSD")
    assert context.data_quality == 0.0


async def test_context_carries_regime_and_summary():
    market = make_market(
        candles=make_candles([100.0, 101.0] * 25), trades=[make_trade(price=100.5)]
    )
    context = await _engine(market).build("BTCUSDT")
    assert context.regime is not None
    summary = context.summary()
    assert summary["regime"] == context.regime.primary.value
    assert "volatility" in summary and "data_quality" in summary

"""Regime Detection: clasificación automática del mercado."""

from app.config.settings import QuantRegimeSettings
from app.engine.models import Regime, RegimeState
from app.engine.regime_detection import RegimeDetector

from tests.unit.quant_helpers import make_candles, make_market

SETTINGS = QuantRegimeSettings(
    timeframe="1m",
    lookback=50,
    trending_efficiency=0.35,
    expansion_ratio=1.4,
    compression_ratio=0.7,
    breakout_lookback=20,
)


def _detect(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> RegimeState:
    market = make_market(candles=make_candles(closes, highs=highs, lows=lows))
    return RegimeDetector(market, SETTINGS).detect("BTCUSDT")


def test_insufficient_candles_is_unknown():
    assert _detect([100.0] * 5).primary is Regime.UNKNOWN


def test_trending_market():
    # Subida casi monótona con leve retroceso final (evita el breakout).
    closes = [100.0 + i for i in range(49)] + [147.2]
    state = _detect(closes)
    assert state.primary is Regime.TRENDING
    assert state.metrics["efficiency_ratio"] > 0.35


def test_ranging_market():
    assert _detect([100.0, 101.0] * 25).primary is Regime.RANGING


def test_breakout_market():
    closes = [100.0] * 45 + [100.5, 101.0, 102.0, 103.0, 105.0]
    state = _detect(closes)
    assert state.primary is Regime.BREAKOUT
    assert state.metrics["breakout_up"] == 1.0


def test_reversal_market():
    up = [100.0 + i * 0.8 for i in range(25)]  # 100 -> 119.2
    down = [119.2 - (i + 1) * 0.9 for i in range(20)]  # 118.3 -> 101.2
    flat = [101.5] * 5  # estabiliza sin marcar nuevo mínimo
    assert _detect(up + down + flat).primary is Regime.REVERSAL


def test_compression_tags():
    closes = [100.0] * 50
    highs = [100.5] * 45 + [100.05] * 5
    lows = [99.5] * 45 + [99.95] * 5
    state = _detect(closes, highs=highs, lows=lows)
    assert Regime.COMPRESSION in state.tags
    assert Regime.LOW_VOLATILITY in state.tags


def test_expansion_tags():
    closes = [100.0] * 50
    highs = [100.1] * 45 + [101.0] * 5
    lows = [99.9] * 45 + [99.0] * 5
    state = _detect(closes, highs=highs, lows=lows)
    assert Regime.EXPANSION in state.tags
    assert Regime.HIGH_VOLATILITY in state.tags


def test_last_state_is_cached():
    market = make_market(candles=make_candles([100.0, 101.0] * 25))
    detector = RegimeDetector(market, SETTINGS)
    assert detector.last_state("BTCUSDT") is None
    state = detector.detect("BTCUSDT")
    assert detector.last_state("BTCUSDT") is state

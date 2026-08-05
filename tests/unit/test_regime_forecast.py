"""Regime Forecast Engine (Bloque 4): clasificación, pronóstico y validación."""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import QuantRegimeForecastSettings
from app.engine.models import Regime, VolatilityState
from app.engine.regime_forecast import OUTCOMES, RegimeForecastEngine, classify_outcome
from app.market.models import Candle, Timeframe

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _candles(closes: list[float], *, spread: float = 0.5) -> list[Candle]:
    """Serie sintética con rango controlado alrededor de cada cierre."""
    return [
        Candle(
            symbol="BTCUSDT",
            provider="binance",
            timeframe=Timeframe.M5,
            start=_START + timedelta(minutes=5 * i),
            end=_START + timedelta(minutes=5 * (i + 1)),
            open=close,
            high=close + spread,
            low=close - spread,
            close=close,
            volume=10.0,
            closed=True,
        )
        for i, close in enumerate(closes)
    ]


def _engine(**overrides) -> RegimeForecastEngine:
    defaults = {"horizon_bars": 3, "min_sample": 5, "confidence_sample": 20}
    return RegimeForecastEngine(QuantRegimeForecastSettings(**{**defaults, **overrides}))


# ---------------------------------------------------------------------------
# Clasificación del desenlace
# ---------------------------------------------------------------------------


def test_outcomes_are_exhaustive_and_mutually_exclusive() -> None:
    # Si una ventana pudiera caer en dos desenlaces, el reparto no sumaría 1 y
    # dejaría de ser un pronóstico.
    candles = _candles([100.0 + i * 0.1 for i in range(40)])
    seen = {classify_outcome(candles, i, 3) for i in range(3, 36)}
    assert seen
    assert seen <= set(OUTCOMES)


def test_a_price_leaving_the_previous_range_is_a_breakout() -> None:
    candles = _candles([100.0] * 5 + [100.0, 105.0, 110.0, 115.0])
    assert classify_outcome(candles, 4, 3) == "breakout"


def test_a_flat_market_is_continuation_not_a_missing_value() -> None:
    assert classify_outcome(_candles([100.0] * 20), 5, 3) == "continuation"


def test_no_classification_without_bars_on_both_sides() -> None:
    candles = _candles([100.0] * 10)
    assert classify_outcome(candles, 1, 3) is None  # sin pasado suficiente
    assert classify_outcome(candles, 9, 3) is None  # sin futuro suficiente


# ---------------------------------------------------------------------------
# Pronóstico
# ---------------------------------------------------------------------------


def test_without_sample_it_declares_ignorance_instead_of_splitting_evenly() -> None:
    # Un reparto uniforme parece un pronóstico y no lo es.
    forecast = _engine().forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL)
    assert forecast.observable is False
    assert forecast.probabilities == {}
    assert "muestra" in forecast.reason


def test_probabilities_always_add_up_to_one() -> None:
    engine = _engine()
    for _ in range(10):
        engine.learn("trending|normal", "continuation")
    forecast = engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL)
    assert forecast.observable is True
    assert sum(forecast.probabilities.values()) == pytest.approx(1.0)
    assert forecast.most_likely == "continuation"


def test_an_unseen_outcome_never_gets_probability_zero() -> None:
    # Con probabilidad 0 el motor afirmaría que algo es imposible por no haberlo
    # visto en unas decenas de observaciones, y el Brier lo castigaría al máximo
    # la primera vez que ocurriera.
    engine = _engine()
    for _ in range(20):
        engine.learn("trending|normal", "continuation")
    forecast = engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL)
    assert all(p > 0.0 for p in forecast.probabilities.values())


def test_conditions_do_not_leak_into_each_other() -> None:
    engine = _engine()
    for _ in range(10):
        engine.learn("trending|normal", "continuation")
        engine.learn("ranging|low", "compression")
    trending = engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL)
    ranging = engine.forecast("BTCUSDT", Regime.RANGING, VolatilityState.LOW)
    assert trending.most_likely == "continuation"
    assert ranging.most_likely == "compression"


def test_confidence_measures_evidence_not_accuracy() -> None:
    # El error clásico es leer la confianza como acierto. Aquí sólo crece con la
    # muestra: si el pronóstico acierta o no lo dice el Brier score.
    engine = _engine(confidence_sample=100)
    for _ in range(10):
        engine.learn("trending|normal", "continuation")
    low = engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL).confidence
    for _ in range(200):
        engine.learn("trending|normal", "reversal")
    high = engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL).confidence
    assert high > low
    assert high == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def test_an_untested_engine_reports_no_quality_at_all() -> None:
    score = _engine().score()
    assert score.resolved == 0
    assert score.brier is None
    assert score.skill is None


def test_a_forecast_is_scored_once_its_horizon_has_elapsed() -> None:
    engine = _engine()
    for _ in range(10):
        engine.learn("trending|normal", "continuation")
    candles = _candles([100.0] * 30)
    forecast = engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL)
    engine.track(forecast, anchor_index=10)
    assert engine.resolve("BTCUSDT", candles) == 1
    score = engine.score()
    assert score.resolved == 1
    assert score.hit_rate == pytest.approx(1.0)
    assert score.brier is not None


def test_a_forecast_without_enough_future_stays_pending() -> None:
    engine = _engine()
    for _ in range(10):
        engine.learn("trending|normal", "continuation")
    candles = _candles([100.0] * 12)
    engine.track(
        engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL), anchor_index=11
    )
    assert engine.resolve("BTCUSDT", candles) == 0
    assert engine.status()["pending"]["BTCUSDT"] == 1


def test_an_unobservable_forecast_is_never_tracked() -> None:
    engine = _engine()
    engine.track(engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL), 5)
    assert engine.status()["pending"] == {}


def test_a_wrong_forecast_is_scored_as_wrong() -> None:
    # El motor cree que todo es continuación; el mercado rompe. El marcador
    # tiene que reflejarlo: un motor que sólo se puntúa cuando acierta no está
    # validando nada.
    engine = _engine()
    for _ in range(20):
        engine.learn("trending|normal", "continuation")
    breaking = _candles([100.0] * 12 + [100.0, 106.0, 112.0, 118.0] + [118.0] * 8)
    engine.track(
        engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL), anchor_index=11
    )
    assert engine.resolve("BTCUSDT", breaking) == 1
    score = engine.score()
    assert score.hit_rate == pytest.approx(0.0)
    assert score.skill is not None  # se publica aunque sea negativo


def test_learning_from_history_is_not_lookahead() -> None:
    # Cada desenlace se clasifica con velas estrictamente posteriores a su
    # ancla: aprender del histórico no puede mirar su propio futuro.
    engine = _engine()
    candles = _candles([100.0 + (i % 7) for i in range(60)])
    learned = engine.learn_from_candles(candles, Regime.RANGING, VolatilityState.NORMAL)
    assert learned > 0
    assert sum(engine.status()["conditions"]["ranging|normal"].values()) == learned


def test_resolving_a_forecast_also_teaches_the_engine() -> None:
    engine = _engine()
    for _ in range(10):
        engine.learn("trending|normal", "continuation")
    before = engine.status()["observations"]
    candles = _candles([100.0] * 30)
    engine.track(
        engine.forecast("BTCUSDT", Regime.TRENDING, VolatilityState.NORMAL), anchor_index=10
    )
    engine.resolve("BTCUSDT", candles)
    assert engine.status()["observations"] == before + 1

"""Correlation Intelligence (Bloque 5): estadística y lectura de relaciones."""

import math

import pytest
from app.config.settings import QuantCorrelationSettings
from app.engine.correlation import CorrelationEngine
from app.engine.correlation.stats import (
    correlation,
    ewma_correlation,
    hedge_ratio,
    lead_lag,
    residual_half_life,
    returns,
)
from app.market.models import Timeframe

from tests.unit.quant_helpers import make_candles

# ---------------------------------------------------------------------------
# Estadística
# ---------------------------------------------------------------------------


def test_returns_skip_non_positive_prices_instead_of_exploding() -> None:
    # Un precio 0 produciría un infinito que contaminaría toda la correlación.
    assert returns([100.0, 110.0]) == pytest.approx([0.1])
    assert returns([100.0, 0.0, 50.0]) == pytest.approx([-1.0])


def test_correlation_is_none_without_variance_not_zero() -> None:
    # 0.0 diría "no se mueven juntas"; sin varianza la correlación no está
    # definida, y son afirmaciones distintas.
    assert correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert correlation([1.0], [1.0]) is None


def test_correlation_recovers_a_known_relationship() -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert correlation(a, a) == pytest.approx(1.0)
    assert correlation(a, [-x for x in a]) == pytest.approx(-1.0)


def test_dynamic_correlation_weighs_the_present_more_than_the_past() -> None:
    # Series que iban juntas y se han separado: la dinámica lo ve, la rodante
    # todavía arrastra el pasado.
    left = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    right = [1.0, 2.0, 3.0, 4.0, -5.0, -6.0, -7.0, -8.0]
    rolling = correlation(left, right)
    dynamic = ewma_correlation(left, right, halflife=2.0)
    assert rolling is not None and dynamic is not None
    assert dynamic < rolling


def test_lead_lag_finds_who_moves_first() -> None:
    leader = [0.0, 1.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0, 0.0, 1.0]
    follower = [0.0] * 2 + leader[:-2]
    best = lead_lag(leader, follower, max_lag=4)
    assert best is not None
    lag, value = best
    assert lag == 2  # positivo: `leader` va por delante
    assert value > 0.9


def test_simultaneous_series_report_no_leadership() -> None:
    series = [0.0, 1.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0]
    best = lead_lag(series, series, max_lag=3)
    assert best is not None
    assert best[0] == 0


def test_hedge_ratio_recovers_a_known_slope() -> None:
    independent = [1.0, 2.0, 3.0, 4.0, 5.0]
    dependent = [2.0 * x + 10.0 for x in independent]
    assert hedge_ratio(dependent, independent) == pytest.approx(2.0)


def test_a_non_reverting_residual_has_no_half_life() -> None:
    # `None` es la respuesta útil para descartar un par. Un número enorme
    # sugeriría una reversión lentísima en vez de ninguna.
    assert residual_half_life([float(i) for i in range(30)]) is None


def test_a_reverting_residual_reports_its_half_life() -> None:
    residual = [10.0 * math.exp(-0.3 * i) for i in range(30)]
    half_life = residual_half_life(residual)
    assert half_life is not None
    assert 1.0 < half_life < 10.0


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


def _engine(series: dict[str, list[float]], **overrides) -> CorrelationEngine:
    """Motor sobre series sintéticas por símbolo."""
    markets = {symbol: make_candles(closes) for symbol, closes in series.items()}

    class _Market:
        def get_candles(self, symbol, timeframe, limit):  # type: ignore[no-untyped-def]
            return markets.get(symbol, [])[-limit:]

    defaults = {"min_sample": 20, "window": 200, "min_correlation": 0.7}
    settings = QuantCorrelationSettings(**{**defaults, **overrides})
    return CorrelationEngine(
        settings, _Market(), list(series), timeframe=Timeframe.M5  # type: ignore[arg-type]
    )


def _wave(n: int, shift: int = 0, sign: float = 1.0) -> list[float]:
    return [100.0 + sign * 5.0 * math.sin((i + shift) / 3.0) for i in range(n)]


def test_correlated_symbols_are_measured_as_correlated() -> None:
    engine = _engine({"A": _wave(120), "B": _wave(120)})
    report = engine.analyze()
    assert len(report.pairs) == 1
    assert report.pairs[0].rolling is not None
    assert report.pairs[0].rolling > 0.9
    assert engine.correlated_with("A") == {"B"}


def test_inverse_symbols_are_correlated_too_by_absolute_value() -> None:
    # Para el filtro, moverse al revés es tan relevante como moverse igual: la
    # exposición se concentra igual.
    engine = _engine({"A": _wave(120), "B": _wave(120, sign=-1.0)})
    engine.analyze()
    assert engine.correlated_with("A") == {"B"}


def test_unrelated_symbols_are_not_reported_as_correlated() -> None:
    engine = _engine({"A": _wave(120), "B": [100.0 + (i % 7) * 0.3 for i in range(120)]})
    engine.analyze()
    assert engine.correlated_with("A") == set()


def test_a_pair_without_sample_is_declared_skipped_not_omitted() -> None:
    # Omitirlo en silencio invitaría a concluir que no están correlacionados.
    report = _engine({"A": _wave(120), "B": _wave(5)}).analyze()
    assert report.pairs == ()
    assert "A~B" in report.skipped


def test_leadership_ignores_lags_backed_by_noise() -> None:
    # Un lag "ganador" con correlación diminuta es ruido con signo.
    engine = _engine(
        {"A": _wave(120), "B": [100.0 + (i % 11) * 0.1 for i in range(120)]},
        min_lead_correlation=0.9,
    )
    assert engine.analyze().leadership == {}


def test_nothing_is_correlated_before_the_first_analysis() -> None:
    # Nunca inventa una correlación que no se ha medido.
    assert _engine({"A": _wave(120), "B": _wave(120)}).correlated_with("A") == set()


def test_divergence_reports_the_change_not_the_level() -> None:
    engine = _engine({"A": _wave(200), "B": _wave(200)})
    pair = engine.analyze().pairs[0]
    assert pair.divergence is not None
    assert pair.divergence == pytest.approx((pair.dynamic or 0.0) - (pair.rolling or 0.0))

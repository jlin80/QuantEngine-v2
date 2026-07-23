"""Indicadores, series, estadística, Feature Lab y Factor Lab (Fase 10)."""

import math

from app.config.settings import FactorLabSettings, FeatureLabSettings
from app.research import indicators as ind
from app.research import stats as st
from app.research.factor_lab import FactorLab
from app.research.feature_lab import FeatureLab

from tests.unit.research_helpers import candles


def test_indicators_warm_up_to_none():
    values = [1.0, 2.0, 3.0]
    assert ind.sma(values, 5) is None
    assert ind.ema(values, 5) is None
    assert ind.rsi(values, 14) is None
    assert ind.sma([1.0, 2.0, 3.0, 4.0], 4) == 2.5


def test_rsi_bounds_and_trend():
    up = [float(i) for i in range(1, 40)]
    assert ind.rsi(up, 14) == 100.0  # sólo subidas
    down = [float(i) for i in range(40, 1, -1)]
    rsi_down = ind.rsi(down, 14)
    assert rsi_down is not None and rsi_down < 5.0


def test_orderflow_primitives():
    data = candles(50, seed=1, flow=0.6)
    assert ind.delta(data[-1]) > 0  # más compra que venta
    pressure = ind.book_pressure(data[-1])
    assert -1.0 <= pressure <= 1.0
    assert ind.cvd(data, 10) is not None


def test_stats_pearson_and_ic():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 4.0, 6.0, 8.0, 10.0]
    assert round(st.pearson(xs, ys), 6) == 1.0
    assert st.pearson(xs, [1.0, 1.0, 1.0, 1.0, 1.0]) == 0.0  # varianza cero


def test_stats_welch_and_stability():
    a = [1.0, 1.1, 0.9, 1.05, 0.95, 1.2, 0.8]
    b = [0.0, 0.1, -0.1, 0.05, -0.05, 0.2, -0.2]
    t_stat, p_value = st.welch_t_test(a, b)
    assert t_stat > 0 and 0.0 <= p_value <= 1.0
    linear = [float(i) for i in range(20)]
    assert st.equity_stability(linear) > 0.99  # crecimiento perfectamente lineal


def test_feature_lab_validates_and_ranks():
    lab = FeatureLab(FeatureLabSettings(min_coverage=0.5, min_abs_ic=0.0))
    reports = lab.validate_all(candles(400, drift=0.0008, seed=5))
    assert len(reports) == len(lab.catalog())
    assert reports == sorted(reports, key=lambda r: abs(r.ic), reverse=True)
    assert all(math.isfinite(r.ic) for r in reports)


def test_feature_lab_rejects_constant_feature():
    lab = FeatureLab(FeatureLabSettings())
    lab.register("constant", lambda cs: [1.0] * len(cs))
    report = lab.validate("constant", candles(200, seed=2))
    assert not report.valid
    assert any("constante" in r or "varianza" in r for r in report.reasons)


def test_factor_lab_ranks_all_families():
    lab = FactorLab(FactorLabSettings(min_abs_ic=0.0))
    ranked = lab.research(candles(500, drift=0.0007, seed=9))
    families = {r.family for r in ranked}
    assert {
        "trend",
        "reversion",
        "liquidity",
        "volatility",
        "temporal",
        "volume",
        "hybrid",
    } <= families
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))

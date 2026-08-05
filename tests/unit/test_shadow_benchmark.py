"""Live Shadow Benchmark (Bloque 15): carriles, gap y honestidad del hueco."""

import pytest
from app.config.settings import ShadowBenchmarkSettings
from app.execution.benchmark import ShadowBenchmark

from tests.unit.test_cost_attribution import _trade


def _benchmark(trades, live=None, opportunity=None, **overrides) -> ShadowBenchmark:
    defaults = {"min_sample": 5}
    settings = ShadowBenchmarkSettings(**{**defaults, **overrides})
    return ShadowBenchmark(
        settings,
        lambda: list(trades),
        None if live is None else (lambda: list(live)),
        None if opportunity is None else (lambda: opportunity),
    )


def _trades(n: int = 10, **kwargs):
    defaults = {"slippage_bps": 6.0, "spread_bps": 4.0, "net": 10.0}
    return [_trade(i, **{**defaults, **kwargs}) for i in range(n)]


# ---------------------------------------------------------------------------
# El carril que no existe
# ---------------------------------------------------------------------------


def test_the_live_track_is_declared_absent_with_its_reason() -> None:
    report = _benchmark(_trades()).analyze()
    live = report.tracks["live"]
    assert live.available is False
    assert "live trading deshabilitado" in live.reason
    assert live.trades == 0


def test_the_fill_difference_is_none_not_zero_without_a_live_track() -> None:
    # Cero diría "paper y live coinciden", que es una afirmación sobre algo que
    # no se ha observado ni una sola vez.
    assert _benchmark(_trades()).analyze().fill_difference_bps is None


def test_the_report_states_that_live_is_disabled() -> None:
    body = _benchmark(_trades()).analyze().to_dict()
    assert body["live_enabled"] is False
    assert any("no lo habilita" in caveat for caveat in body["caveats"])


def test_the_benchmark_exposes_the_invariant_for_outside_verification() -> None:
    assert _benchmark(_trades()).status()["live_enabled"] is False


# ---------------------------------------------------------------------------
# Paper frente al ideal
# ---------------------------------------------------------------------------


def test_the_ideal_track_is_paper_plus_what_execution_cost() -> None:
    # No es una simulación aparte: un segundo modelo introduciría una
    # diferencia que vendría de la discrepancia entre modelos, no de la
    # ejecución.
    report = _benchmark(_trades()).analyze()
    paper = report.tracks["paper"]
    ideal = report.tracks["ideal"]
    assert ideal.net_pnl > paper.net_pnl
    assert ideal.net_pnl == pytest.approx(paper.net_pnl + (report.execution_gap_money or 0.0))


def test_the_ideal_track_has_no_execution_cost_by_definition() -> None:
    ideal = _benchmark(_trades()).analyze().tracks["ideal"]
    assert ideal.avg_slippage_bps == 0.0
    assert ideal.avg_spread_bps == 0.0


def test_the_gap_is_the_sum_of_slippage_and_spread() -> None:
    report = _benchmark(_trades(slippage_bps=6.0, spread_bps=4.0)).analyze()
    assert report.execution_gap_bps == pytest.approx(10.0)


def test_the_gap_in_money_is_computed_over_units_not_lots() -> None:
    # Mismo criterio que ADR-099: en oro un lote son 100 onzas.
    one = _benchmark(_trades(contract_size=1.0)).analyze().execution_gap_money
    gold = _benchmark(_trades(contract_size=100.0)).analyze().execution_gap_money
    assert one is not None and gold is not None
    assert gold == pytest.approx(one * 100.0)


def test_no_trades_produces_an_empty_report_not_a_crash() -> None:
    report = _benchmark([]).analyze()
    assert report.execution_gap_bps is None
    assert report.tracks["paper"].available is False


# ---------------------------------------------------------------------------
# Recomendaciones
# ---------------------------------------------------------------------------


def test_a_thin_sample_produces_no_recommendations_at_all() -> None:
    # Una recomendación es una llamada a la acción: emitirla sobre diez
    # operaciones es peor que callarse.
    report = _benchmark(_trades(3), min_sample=100).analyze()
    assert report.recommendations == ()
    assert any("no se emiten recomendaciones" in caveat for caveat in report.caveats)


def test_an_expensive_execution_is_flagged() -> None:
    report = _benchmark(
        _trades(20, slippage_bps=30.0, spread_bps=10.0), high_gap_bps=15.0
    ).analyze()
    assert any("coste de ejecución modelado" in r for r in report.recommendations)


def test_execution_eating_the_edge_is_called_out_as_an_execution_problem() -> None:
    # El edge es real pero está mal capturado, y eso se arregla en ejecución.
    report = _benchmark(
        _trades(20, slippage_bps=30.0, spread_bps=10.0, net=1.0), high_edge_share=0.3
    ).analyze()
    assert any("mal capturado" in r for r in report.recommendations)


def test_every_report_repeats_that_the_gap_is_only_a_baseline() -> None:
    # Sin este recordatorio, el gap modelado se lee como slippage real medido.
    report = _benchmark(_trades(20)).analyze()
    assert any("línea base" in r for r in report.recommendations)
    assert any("no una medición independiente" in c for c in report.caveats)


def test_the_opportunity_cost_comes_from_where_it_was_measured() -> None:
    assert _benchmark(_trades(), opportunity=3.5).analyze().opportunity_cost_r == 3.5
    assert _benchmark(_trades()).analyze().opportunity_cost_r is None

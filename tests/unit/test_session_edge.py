"""Edge por (estrategia, sesión): estadística y kill criteria (Bloque 3)."""

from datetime import UTC, datetime, timedelta

import pytest
from app.backtesting.session_edge import (
    CellSample,
    benjamini_hochberg,
    bootstrap_difference,
    bootstrap_expectancy,
    evaluate_cells,
    profit_factor,
    scalping_check,
    session_cell,
    verdict_summary,
    walk_forward,
)

_START = datetime(2026, 6, 22, tzinfo=UTC)


def _boundaries() -> list[datetime]:
    return [_START + timedelta(days=10), _START + timedelta(days=20)]


def _sample(strategy: str, session: str, values: list[float], *, spread_days=(1, 11, 21)):
    """Muestra con operaciones repartidas por los tres sub-periodos."""
    sample = CellSample(strategy=strategy, session=session)
    for i, value in enumerate(values):
        sample.trade_r.append(value)
        sample.trade_times.append(_START + timedelta(days=spread_days[i % 3], hours=i))
        sample.signal_r.append(value)
    return sample


# --------------------------------------------------------------------------
# Celdas de sesión
# --------------------------------------------------------------------------


def test_session_cell_keeps_overlaps_as_their_own_cell():
    assert session_cell(["europe", "america"]) == "america+europe"
    assert session_cell(["asia"]) == "asia"


def test_session_cell_distinguishes_missing_data_from_no_session():
    """`off` es una medición; `desconocida` es su ausencia. No son lo mismo."""
    assert session_cell([]) == "off"
    assert session_cell(None) == "desconocida"


# --------------------------------------------------------------------------
# Estadística
# --------------------------------------------------------------------------


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    values = [0.5, -1.0, 1.2, -1.0, 0.8, 2.0] * 6
    low, high, p_value = bootstrap_expectancy(values, resamples=2000)
    again = bootstrap_expectancy(values, resamples=2000)
    assert (low, high, p_value) == again  # una cifra que cambia no es reportable
    assert low < sum(values) / len(values) < high
    assert 0.0 <= p_value <= 1.0


def test_bootstrap_of_a_clearly_losing_cell_gives_high_p_value():
    low, _, p_value = bootstrap_expectancy([-1.0] * 40, resamples=1000)
    assert low < 0
    assert p_value == 1.0


def test_bootstrap_difference_is_deterministic_and_brackets_the_gap():
    treatment = [0.5, -1.0, 1.2, -1.0, 0.8, 2.0] * 8
    control = [0.1, -1.0, 0.3, -1.0, 0.2, 0.4] * 8
    low, high, p_value = bootstrap_difference(treatment, control, resamples=2000)
    again = bootstrap_difference(treatment, control, resamples=2000)
    assert (low, high, p_value) == again  # una cifra que cambia no es reportable

    observed = sum(treatment) / len(treatment) - sum(control) / len(control)
    assert low < observed < high
    assert 0.0 <= p_value <= 1.0


def test_bootstrap_difference_finds_no_gap_between_identical_arms():
    """Dos brazos iguales tienen que dar un IC que contenga el cero.

    Es el caso que protege del falso positivo: si esto excluyera cero, el script
    del A/B declararia un efecto donde solo hay remuestreo.
    """
    arm = [0.4, -1.0, 1.1, -1.0, 0.7] * 10
    low, high, _ = bootstrap_difference(arm, list(arm), resamples=2000)
    assert low < 0.0 < high


def test_bootstrap_difference_detects_a_clearly_better_arm():
    low, _, p_value = bootstrap_difference([1.0] * 60, [-1.0] * 60, resamples=1000)
    assert low > 0.0
    assert p_value == 0.0


def test_bootstrap_difference_rejects_an_empty_arm():
    with pytest.raises(ValueError):
        bootstrap_difference([], [1.0, 2.0])
    with pytest.raises(ValueError):
        bootstrap_difference([1.0, 2.0], [])


def test_benjamini_hochberg_rejects_less_than_raw_threshold():
    """El punto de la corrección: p<0.05 sueltos dejan de bastar."""
    p_values = [0.001, 0.04, 0.045, 0.20, 0.5, 0.9]
    accepted = benjamini_hochberg(p_values, q=0.10)
    assert accepted[0] is True
    assert accepted[4] is False and accepted[5] is False
    assert sum(accepted) < sum(1 for p in p_values if p < 0.05) + 1


def test_benjamini_hochberg_handles_empty_input():
    assert benjamini_hochberg([]) == []


def test_profit_factor_without_losses_is_not_zero():
    assert profit_factor([1.0, 2.0]) == float("inf")
    assert profit_factor([]) is None
    assert profit_factor([2.0, -1.0]) == 2.0


# --------------------------------------------------------------------------
# Kill criteria
# --------------------------------------------------------------------------


def test_small_sample_is_reported_not_dropped_nor_filled():
    samples = [_sample("bos", "asia", [1.0, 2.0, 3.0])]
    results = evaluate_cells(samples, boundaries=_boundaries(), resamples=200)
    assert len(results) == 1
    assert results[0].verdict == "muestra_insuficiente"
    # Ni se omite ni se rellena: la expectancy cruda sigue ahí, el veredicto no.
    assert results[0].expectancy_r == 2.0
    assert results[0].p_value is None


def test_cell_that_only_wins_in_one_subperiod_is_unstable_not_stable():
    values = [3.0] * 20 + [-1.0] * 20  # todo lo bueno en el primer tercio
    sample = CellSample(strategy="mss", session="europe")
    for i, value in enumerate(values):
        sample.trade_r.append(value)
        day = 1 if i < 20 else 15
        sample.trade_times.append(_START + timedelta(days=day, minutes=i))
        sample.signal_r.append(value)
    results = evaluate_cells([sample], boundaries=_boundaries(), resamples=500)
    assert results[0].verdict != "edge_estable"
    assert results[0].stable_across_subperiods is False


def test_a_subperiod_without_trades_does_not_count_as_confirmation():
    """Ausencia de evidencia no es evidencia: `None` no confirma estabilidad."""
    sample = CellSample(strategy="bos", session="asia")
    for i in range(40):
        sample.trade_r.append(1.0)
        sample.trade_times.append(_START + timedelta(days=1, minutes=i))
        sample.signal_r.append(1.0)
    results = evaluate_cells([sample], boundaries=_boundaries(), resamples=500)
    assert results[0].subperiod_expectancy[1] is None
    assert results[0].stable_across_subperiods is False
    assert results[0].verdict == "inestable"


def test_consistently_winning_cell_is_declared_stable():
    sample = _sample("bos", "europe", [1.0, 0.8, 1.2] * 15)
    results = evaluate_cells([sample], boundaries=_boundaries(), resamples=500)
    assert results[0].verdict == "edge_estable"
    assert results[0].ci_low is not None and results[0].ci_low > 0


def test_one_winner_alone_does_not_justify_wiring_weights():
    """Con FDR q=0.10 se espera ≈1 falso positivo: una celda no es señal."""
    winner = _sample("bos", "europe", [1.0, 0.8, 1.2] * 15)
    losers = [_sample(f"s{i}", "asia", [-1.0, -0.5, -0.8] * 15) for i in range(5)]
    results = evaluate_cells([winner, *losers], boundaries=_boundaries(), resamples=500)
    summary = verdict_summary(results)
    assert summary["decision"] == "insuficiente_para_segmentar"


def test_winners_all_in_one_session_do_not_justify_segmenting():
    winners = [_sample(f"s{i}", "europe", [1.0, 0.8, 1.2] * 15) for i in range(4)]
    results = evaluate_cells(winners, boundaries=_boundaries(), resamples=500)
    summary = verdict_summary(results)
    assert summary["decision"] == "senal_en_una_franja"


def test_no_winners_is_a_verdict_not_a_failure():
    losers = [_sample(f"s{i}", "asia", [-1.0, -0.5, -0.8] * 15) for i in range(4)]
    results = evaluate_cells(losers, boundaries=_boundaries(), resamples=500)
    summary = verdict_summary(results)
    assert summary["decision"] == "no_hay_edge_estable_por_sesion"
    assert summary["counts"]["sin_edge"] == 4


# --------------------------------------------------------------------------
# Bloque 1 — walk-forward IS → OOS
# --------------------------------------------------------------------------


def _timed(strategy: str, session: str, values: list[float]):
    """Muestra con una operación por hora, en orden cronológico real.

    A diferencia de ``_sample``, aquí el orden importa: el walk-forward parte
    por fecha, y repartir las operaciones en ciclo entre sub-periodos haría que
    "antes de la frontera" no significara nada.
    """
    sample = CellSample(strategy=strategy, session=session)
    for i, value in enumerate(values):
        sample.trade_r.append(value)
        sample.trade_times.append(_START + timedelta(hours=i))
        sample.signal_r.append(value)
    return sample


def test_walk_forward_selects_in_sample_and_measures_out_of_sample():
    """La celda gana en IS y pierde en OOS: el WF tiene que enseñar la caída."""
    sample = _timed("bos", "europe", [1.0] * 40 + [-1.0] * 40)
    boundary = _START + timedelta(hours=40)
    folds = walk_forward([sample], boundaries=[boundary], min_trades=30, resamples=500)
    assert len(folds) == 1
    assert folds[0]["cells_selected_in_sample"] == ["bos@europe"]
    assert folds[0]["oos_trades"] == 40
    assert folds[0]["oos_expectancy_r"] == -1.0


def test_walk_forward_reports_an_empty_selection_instead_of_skipping_the_fold():
    """Sin celdas elegidas el pliegue se reporta igual: es un resultado."""
    sample = _timed("bos", "europe", [-1.0] * 80)
    boundary = _START + timedelta(hours=40)
    folds = walk_forward([sample], boundaries=[boundary], min_trades=30, resamples=500)
    assert folds[0]["cells_selected_in_sample"] == []
    assert folds[0]["oos_trades"] == 0
    assert folds[0]["oos_expectancy_r"] is None


def test_walk_forward_ignores_cells_without_in_sample_evidence():
    """Una celda que solo opera DESPUÉS de la frontera no se puede seleccionar."""
    late = CellSample(strategy="late", session="asia")
    for i in range(60):
        late.trade_r.append(1.0)
        late.trade_times.append(_START + timedelta(hours=50 + i))
    folds = walk_forward([late], boundaries=[_START + timedelta(hours=40)], resamples=500)
    assert folds[0]["cells_eligible_in_sample"] == 0
    assert folds[0]["cells_selected_in_sample"] == []


# --------------------------------------------------------------------------
# Bloque 7 — régimen de scalping
# --------------------------------------------------------------------------


def _with_exits(strategy: str, values: list[float], holding_s: float, reason: str):
    sample = _sample(strategy, "europe", values)
    sample.trade_holding_s.extend([holding_s] * len(values))
    sample.trade_exit.extend([reason] * len(values))
    return sample


def test_scalping_check_passes_when_holdings_are_short():
    sample = _with_exits("bos", [1.0, 0.8, 1.2] * 15, 180.0, "take_profit")
    results = evaluate_cells([sample], boundaries=_boundaries(), resamples=500)
    check = scalping_check(results, max_holding_s=1800.0)
    assert check["decision"] == "dentro_del_regimen_de_scalping"
    assert check["holding_median_s"] == 180.0
    assert check["cells_cut_before_thesis"] == []


def test_scalping_check_flags_edge_that_only_appears_with_long_holdings():
    """Es la pregunta literal del Bloque 7: edge, sí, pero fuera de scalping."""
    sample = _with_exits("bos", [1.0, 0.8, 1.2] * 15, 7200.0, "take_profit")
    results = evaluate_cells([sample], boundaries=_boundaries(), resamples=500)
    check = scalping_check(results, max_holding_s=1800.0)
    assert check["decision"] == "edge_solo_con_holdings_largos"
    assert check["cells_with_edge_over_threshold"] == ["bos@europe"]


def test_scalping_check_flags_trades_cut_before_their_thesis_resolves():
    """Holding corto NO prueba scalping: puede ser que algo lo esté cortando."""
    sample = _with_exits("bos", [-0.2, -0.1, -0.3] * 15, 200.0, "regime_change")
    results = evaluate_cells([sample], boundaries=_boundaries(), resamples=500)
    check = scalping_check(results, max_holding_s=1800.0)
    assert check["decision"] == "dentro_del_regimen_de_scalping"
    assert check["cells_cut_before_thesis"] == ["bos@europe"]

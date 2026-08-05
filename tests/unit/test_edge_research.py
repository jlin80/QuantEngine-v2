"""Edge Research Engine (Bloque 1): métricas, clasificación y persistencia."""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import QuantEdgeResearchSettings
from app.engine.edge_research import (
    STATUS_DEGRADING,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT,
    EdgeReportHistory,
    EdgeResearchEngine,
    EdgeResearchReport,
)
from app.engine.edge_research import metrics as m
from app.engine.evaluation.outcomes import VirtualOutcome

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _outcome(index: int, r: float, *, strategy: str = "alpha", confidence: float | None = 0.6):
    """Una resolución virtual sintética, ordenada por índice."""
    opened = _START + timedelta(minutes=index)
    return VirtualOutcome(
        signal_id=f"{strategy}-{index}",
        strategy=strategy,
        symbol="BTCUSDm",
        direction="long",
        entry=100.0,
        stop=99.0,
        target=102.0,
        r_multiple=r,
        outcome="win" if r > 0 else "loss",
        false_signal=False,
        opened_at=opened,
        closed_at=opened + timedelta(minutes=5),
        confidence=confidence,
    )


def _engine(outcomes, tmp_path=None, **overrides) -> EdgeResearchEngine:
    """Motor sobre una lista fija de resoluciones."""
    settings = QuantEdgeResearchSettings(min_sample=10, blocks=4, **overrides)
    history = EdgeReportHistory(
        None if tmp_path is None else tmp_path / "edge.jsonl",
        persist=tmp_path is not None,
    )
    return EdgeResearchEngine(settings, lambda: list(outcomes), history)


# ---------------------------------------------------------------------------
# Métricas puras
# ---------------------------------------------------------------------------


def test_expectancy_and_profit_factor_over_a_known_series() -> None:
    values = [1.0, -1.0, 2.0, -1.0]
    assert m.expectancy(values) == pytest.approx(0.25)
    assert m.profit_factor(values) == pytest.approx(1.5)


def test_metrics_return_none_instead_of_zero_without_sample() -> None:
    # La distinción es la razón de ser de los opcionales: "no medible" no puede
    # colapsarse a 0.0, o un consumidor apagará estrategias por falta de datos.
    assert m.expectancy([]) is None
    assert m.profit_factor([1.0, 2.0]) is None  # sin pérdidas
    assert m.sharpe([1.0]) is None
    assert m.sortino([1.0, 2.0]) is None  # sin resultados negativos
    assert m.ols_slope([1.0]) is None


def test_max_drawdown_measures_the_worst_fall_from_the_peak() -> None:
    assert m.max_drawdown([1.0, 1.0, -3.0, 0.5]) == pytest.approx(3.0)
    assert m.max_drawdown([]) == pytest.approx(0.0)


def test_blocks_keep_the_most_recent_trades_when_the_split_is_uneven() -> None:
    # 7 valores en 3 bloques = bloques de 2; se descarta el MÁS ANTIGUO.
    blocks = m.block_expectancies([9.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0], 3)
    assert blocks == [1.0, 2.0, 3.0]


def test_edge_decay_is_positive_when_the_edge_shrinks() -> None:
    decaying = [1.0, 1.0, 0.5, 0.5, 0.0, 0.0, -0.5, -0.5]
    improving = list(reversed(decaying))
    assert (m.edge_decay(decaying, 4) or 0.0) > 0.0
    assert (m.edge_decay(improving, 4) or 0.0) < 0.0


def test_half_life_is_none_when_the_edge_is_not_decaying() -> None:
    assert m.half_life([0.5] * 20, 4) is None


def test_half_life_counts_trades_not_blocks() -> None:
    # 4 bloques de 5 operaciones, expectativa cayendo 0.25 R por bloque desde
    # 0.5: media vida = (0.5/2)/0.25 = 1 bloque = 5 operaciones.
    values = [1.25] * 5 + [1.0] * 5 + [0.75] * 5 + [0.5] * 5
    assert m.half_life(values, 4) == pytest.approx(5.0)


def test_stability_and_persistence_separate_consistency_from_luck() -> None:
    steady = [0.2] * 20
    lucky = [3.0] * 5 + [-0.5] * 15  # misma expectativa positiva, un solo bloque bueno
    assert (m.stability_score(steady, 4) or 0.0) > (m.stability_score(lucky, 4) or 1.0)
    assert m.edge_persistence(steady, 4) == pytest.approx(1.0)
    assert m.edge_persistence(lucky, 4) == pytest.approx(0.25)


def test_confidence_drift_detects_a_strategy_getting_cockier() -> None:
    rising = [0.4] * 5 + [0.6] * 5 + [0.8] * 5 + [0.9] * 5
    assert (m.confidence_drift(rising, 4) or 0.0) > 0.0


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


def test_insufficient_sample_reports_none_metrics_not_zeros() -> None:
    engine = _engine([_outcome(i, 1.0) for i in range(5)])
    report = engine.generate().by_strategy()["alpha"]
    assert report.status == STATUS_INSUFFICIENT
    assert report.expectancy_r is None
    assert report.health_score is None
    assert report.reasons  # siempre auditable


def test_a_steadily_profitable_strategy_is_healthy() -> None:
    engine = _engine([_outcome(i, 0.4 if i % 3 else -0.5) for i in range(40)])
    report = engine.generate().by_strategy()["alpha"]
    assert report.status == STATUS_HEALTHY
    assert (report.expectancy_r or 0.0) > 0.0
    assert (report.health_score or 0.0) > 50.0


def test_a_dying_edge_is_flagged_as_degrading() -> None:
    # Empieza ganando, termina perdiendo: decay alto, persistencia baja.
    values = [1.0] * 10 + [0.5] * 10 + [-0.5] * 10 + [-1.0] * 10
    engine = _engine([_outcome(i, v) for i, v in enumerate(values)])
    report = engine.generate().by_strategy()["alpha"]
    assert report.status == STATUS_DEGRADING
    assert (report.edge_decay or 0.0) > 0.0
    assert len(report.reasons) >= 2  # `degrading` exige motivos concurrentes


def test_one_metric_out_of_range_is_only_a_watch() -> None:
    # Un único motivo no basta: con muestras de este tamaño, una sola métrica
    # fuera de rango es ruido, y tratarla como deterioro llenaría de falsas
    # alarmas al Meta Strategy Manager.
    engine = _engine([_outcome(i, 0.4 if i % 3 else -0.5) for i in range(40)], min_stability=0.99)
    report = engine.generate().by_strategy()["alpha"]
    assert report.status == "watch"
    assert len(report.reasons) == 1


def test_ingest_is_idempotent_so_the_sample_is_not_inflated() -> None:
    outcomes = [_outcome(i, 0.2) for i in range(40)]
    engine = _engine(outcomes)
    engine.generate()
    engine.generate()
    engine.generate()
    assert engine.status()["samples"]["alpha"] == 40


def test_rolling_window_forgets_the_distant_past() -> None:
    outcomes = [_outcome(i, 1.0) for i in range(50)] + [_outcome(i, -1.0) for i in range(50, 100)]
    engine = _engine(outcomes, rolling_window=40)
    report = engine.generate().by_strategy()["alpha"]
    # La ventana sólo ve las últimas 40, todas perdedoras: el acumulado (que
    # daría expectativa 0) no puede seguir tapando un edge muerto.
    assert report.sample == 40
    assert (report.expectancy_r or 0.0) < 0.0


def test_strategies_are_measured_independently() -> None:
    outcomes = [_outcome(i, 0.5, strategy="alpha") for i in range(40)]
    outcomes += [_outcome(i, -0.5, strategy="beta") for i in range(40)]
    report = _engine(outcomes).generate().by_strategy()
    assert (report["alpha"].expectancy_r or 0.0) > 0.0
    assert (report["beta"].expectancy_r or 0.0) < 0.0


def test_confidence_drift_is_skipped_when_the_rows_predate_the_block() -> None:
    # Filas escritas antes del Bloque 1 no traen confianza: la métrica se omite
    # en vez de rellenarse con un valor inventado.
    engine = _engine([_outcome(i, 0.2, confidence=None) for i in range(40)])
    assert engine.generate().by_strategy()["alpha"].confidence_drift is None


def test_factor_never_penalises_a_strategy_without_evidence() -> None:
    engine = _engine([_outcome(i, 0.2) for i in range(5)])
    engine.generate()
    assert engine.factor("alpha") == 1.0
    assert engine.factor("jamas-vista") == 1.0


def test_factor_never_falls_below_the_configured_floor() -> None:
    values = [1.0] * 10 + [-1.0] * 30
    engine = _engine([_outcome(i, v) for i, v in enumerate(values)], factor_floor=0.5)
    engine.generate()
    assert 0.5 <= engine.factor("alpha") < 1.0


# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------


def test_history_persists_and_reloads_reports(tmp_path) -> None:
    engine = _engine([_outcome(i, 0.3) for i in range(40)], tmp_path=tmp_path)
    engine.generate()
    engine.generate()
    reloaded = list(engine.history.load())
    assert len(reloaded) == 2
    assert reloaded[0].by_strategy()["alpha"].sample == 40


def test_history_series_tracks_one_strategy_over_time(tmp_path) -> None:
    engine = _engine([_outcome(i, 0.3) for i in range(40)], tmp_path=tmp_path)
    engine.generate()
    engine.generate()
    points = engine.history.series("alpha")
    assert len(points) == 2
    assert all("at" in point and "health_score" in point for point in points)


def test_corrupt_history_lines_do_not_hide_the_rest(tmp_path) -> None:
    path = tmp_path / "edge.jsonl"
    history = EdgeReportHistory(path)
    history.record(EdgeResearchReport())
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{no es json\n")
    history.record(EdgeResearchReport())
    assert len(list(history.load())) == 2


def test_a_disk_failure_never_kills_the_engine(tmp_path, monkeypatch) -> None:
    history = EdgeReportHistory(tmp_path / "edge.jsonl")

    def _boom(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr("pathlib.Path.open", _boom)
    history.record(EdgeResearchReport())
    assert history.status()["dropped"] == 1
    assert history.recorded == 1

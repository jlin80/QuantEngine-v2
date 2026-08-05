"""Confidence Calibration Engine (Bloque 10): curva, ECE, sesgo y corrección."""

import pytest
from app.config.settings import MLCalibrationSettings
from app.ml.calibration import ConfidenceCalibrationEngine


def _engine(**overrides) -> ConfidenceCalibrationEngine:
    defaults = {"min_sample": 20, "min_bin_sample": 2, "bins": 5}
    return ConfidenceCalibrationEngine(MLCalibrationSettings(**{**defaults, **overrides}))


def _feed(engine: ConfidenceCalibrationEngine, confidence: float, hits: int, misses: int) -> None:
    """Declara `confidence` y hace que acierte `hits` veces de `hits+misses`."""
    engine.observe_many([(confidence, True)] * hits + [(confidence, False)] * misses)


def test_without_sample_nothing_is_concluded_and_nothing_is_corrected() -> None:
    # Sin evidencia no se toca la confianza de nadie.
    report = _engine(min_sample=100).analyze()
    assert report.observable is False
    assert report.correction == 1.0
    assert report.ece is None
    assert "muestra" in report.reason


def test_a_perfectly_calibrated_system_shows_no_bias() -> None:
    engine = _engine()
    _feed(engine, 0.8, hits=80, misses=20)
    _feed(engine, 0.4, hits=40, misses=60)
    report = engine.analyze()
    assert report.observable is True
    assert report.bias == pytest.approx(0.0, abs=0.02)
    assert report.ece is not None
    assert report.ece < 0.05
    assert report.correction == pytest.approx(1.0, abs=0.05)


def test_overconfidence_is_detected_with_its_sign() -> None:
    # Declara 0.9 y acierta la mitad: promete de más.
    engine = _engine()
    _feed(engine, 0.9, hits=50, misses=50)
    report = engine.analyze()
    assert report.bias is not None and report.bias < 0.0
    assert report.overconfidence is not None and report.overconfidence > 0.0
    assert report.correction < 1.0


def test_underconfidence_is_detected_too() -> None:
    engine = _engine()
    _feed(engine, 0.3, hits=80, misses=20)
    report = engine.analyze()
    assert report.bias is not None and report.bias > 0.0
    assert report.underconfidence is not None and report.underconfidence > 0.0
    assert report.correction > 1.0


def test_the_curve_reports_each_bin_with_its_sample() -> None:
    engine = _engine()
    _feed(engine, 0.85, hits=30, misses=10)
    _feed(engine, 0.25, hits=10, misses=30)
    bins = engine.analyze().bins
    assert len(bins) == 2
    assert all(b.sample > 0 for b in bins)
    assert sum(b.sample for b in bins) == 80


def test_thin_bins_are_dropped_so_they_do_not_drag_the_ece() -> None:
    # Un tramo con tres observaciones da una tasa de 0.0 o 0.67 y arrastra el
    # ECE con ruido que no significa nada.
    engine = _engine(min_bin_sample=10)
    _feed(engine, 0.85, hits=30, misses=10)
    _feed(engine, 0.05, hits=0, misses=3)
    bins = engine.analyze().bins
    assert all(b.lower >= 0.8 for b in bins)


def test_the_correction_is_capped_so_a_bad_month_is_not_a_rebuild() -> None:
    # Sin techo, una racha de 60 operaciones puede producir un factor de 0.4 que
    # apagaría medio sistema.
    engine = _engine(min_correction=0.7, max_correction=1.3)
    _feed(engine, 0.95, hits=2, misses=98)
    assert engine.analyze().correction == pytest.approx(0.7)


def test_the_brier_score_punishes_confident_mistakes_most() -> None:
    confident_and_wrong = _engine()
    _feed(confident_and_wrong, 0.95, hits=20, misses=80)
    humble_and_wrong = _engine()
    _feed(humble_and_wrong, 0.55, hits=20, misses=80)
    assert (confident_and_wrong.analyze().brier or 0.0) > (humble_and_wrong.analyze().brier or 0.0)


def test_the_correction_is_applied_only_when_asked() -> None:
    # No corrige su propia entrada en silencio: quien lo consuma decide.
    engine = _engine()
    _feed(engine, 0.9, hits=50, misses=50)
    assert engine.calibrated(0.9) == 0.9  # aún sin informe
    engine.analyze()
    assert engine.calibrated(0.9) < 0.9


def test_calibrating_without_an_observable_report_changes_nothing() -> None:
    engine = _engine(min_sample=1000)
    _feed(engine, 0.9, hits=5, misses=5)
    engine.analyze()
    assert engine.calibrated(0.77) == 0.77


def test_the_calibrated_value_never_leaves_zero_to_one() -> None:
    engine = _engine(max_correction=3.0)
    _feed(engine, 0.3, hits=100, misses=0)
    engine.analyze()
    assert engine.calibrated(0.95) <= 1.0


def test_the_reliability_diagram_ships_ready_to_plot() -> None:
    # El dashboard no debería tener que reconstruir la referencia y arriesgarse
    # a dibujarla mal.
    engine = _engine()
    _feed(engine, 0.8, hits=40, misses=10)
    diagram = engine.analyze().to_dict()["reliability_diagram"]
    assert diagram
    assert set(diagram[0]) == {"declared", "observed"}


def test_the_window_forgets_old_observations() -> None:
    engine = _engine(window=50, min_sample=10)
    _feed(engine, 0.9, hits=100, misses=0)
    _feed(engine, 0.1, hits=0, misses=50)
    report = engine.analyze()
    assert report.sample == 50
    assert report.bins[0].upper <= 0.2

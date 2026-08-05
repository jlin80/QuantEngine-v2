"""Feature Importance Tracker (Bloque 13): permutación, historia y decaimiento."""

import random

import pytest
from app.config.settings import MLImportanceSettings
from app.ml.importance import FeatureImportanceTracker
from app.ml.models.tree import DecisionTreeModel


def _tracker(**overrides) -> FeatureImportanceTracker:
    defaults = {"min_sample": 10, "repeats": 3}
    settings = MLImportanceSettings(**{**defaults, **overrides})
    return FeatureImportanceTracker(settings, rng=random.Random(0))


def _dataset(n: int = 200):
    """La feature 0 decide la etiqueta; la 1 y la 2 son ruido puro."""
    rng = random.Random(7)
    x: list[list[float]] = []
    y: list[int] = []
    for _ in range(n):
        signal = rng.choice([0.0, 1.0])
        x.append([signal, rng.random(), rng.random()])
        y.append(int(signal))
    return x, y


def _fitted_model(x, y) -> DecisionTreeModel:
    model = DecisionTreeModel(max_depth=3)
    model.fit(x, y)
    return model


def test_the_feature_that_decides_the_label_ranks_first() -> None:
    x, y = _dataset()
    report = _tracker().measure(_fitted_model(x, y), x, y, ["signal", "noise_a", "noise_b"])
    assert report.observable is True
    assert report.features[0].feature == "signal"
    assert report.features[0].current > 0.3


def test_pure_noise_scores_near_zero() -> None:
    x, y = _dataset()
    report = _tracker().measure(_fitted_model(x, y), x, y, ["signal", "noise_a", "noise_b"])
    noise = [f for f in report.features if f.feature.startswith("noise")]
    assert all(f.current < 0.1 for f in noise)


def test_importance_is_never_reported_as_negative() -> None:
    # Una "importancia negativa" sólo significa que barajar mejoró el modelo por
    # azar; reportarla invita a interpretar que la feature estorba.
    x, y = _dataset()
    report = _tracker().measure(_fitted_model(x, y), x, y)
    assert all(f.current >= 0.0 for f in report.features)


def test_an_untrained_model_cannot_be_measured() -> None:
    x, y = _dataset()
    report = _tracker().measure(DecisionTreeModel(), x, y)
    assert report.observable is False
    assert "no está entrenado" in report.reason


def test_a_thin_sample_is_declared_not_guessed() -> None:
    x, y = _dataset(5)
    report = _tracker(min_sample=50).measure(_fitted_model(x, y), x, y)
    assert report.observable is False
    assert "muestra" in report.reason


def test_the_first_measurement_has_no_history_to_compare_against() -> None:
    x, y = _dataset()
    report = _tracker().measure(_fitted_model(x, y), x, y)
    assert all(f.historical is None for f in report.features)
    assert all(f.change is None for f in report.features)
    assert all(f.samples == 0 for f in report.features)


def test_the_second_measurement_compares_against_the_first() -> None:
    x, y = _dataset()
    model = _fitted_model(x, y)
    tracker = _tracker()
    tracker.measure(model, x, y, ["signal", "noise_a", "noise_b"])
    second = tracker.measure(model, x, y, ["signal", "noise_a", "noise_b"])
    signal = next(f for f in second.features if f.feature == "signal")
    assert signal.historical is not None
    assert signal.change is not None
    assert signal.samples == 1


def test_a_measurement_does_not_enter_its_own_reference() -> None:
    # Si la historia se actualizara antes de comparar, el cambio saldría
    # sistemáticamente amortiguado.
    x, y = _dataset()
    model = _fitted_model(x, y)
    tracker = _tracker()
    first = tracker.measure(model, x, y, ["signal", "noise_a", "noise_b"])
    second = tracker.measure(model, x, y, ["signal", "noise_a", "noise_b"])
    signal_first = next(f for f in first.features if f.feature == "signal")
    signal_second = next(f for f in second.features if f.feature == "signal")
    assert signal_second.historical == pytest.approx(signal_first.current)


def test_a_feature_that_stops_contributing_shows_decay() -> None:
    strong_x, strong_y = _dataset()
    tracker = _tracker()
    names = ["signal", "noise_a", "noise_b"]
    tracker.measure(_fitted_model(strong_x, strong_y), strong_x, strong_y, names)

    # Ahora la etiqueta ya no depende de la feature 0: su aporte desaparece.
    rng = random.Random(3)
    weak_x = [[rng.choice([0.0, 1.0]), rng.random(), rng.random()] for _ in range(200)]
    weak_y = [rng.choice([0, 1]) for _ in range(200)]
    report = tracker.measure(_fitted_model(weak_x, weak_y), weak_x, weak_y, names)

    signal = next(f for f in report.features if f.feature == "signal")
    assert signal.decay is not None
    assert signal.decay > 0.5
    assert "signal" in [f.feature for f in report.decaying]


def test_native_importance_is_reported_apart_from_permutation() -> None:
    # Miden cosas distintas: cuánto usa el modelo una feature frente a cuánto se
    # pierde si desaparece. Promediarlas no respondería a ninguna de las dos.
    x, y = _dataset()
    report = _tracker().measure(_fitted_model(x, y), x, y)
    assert all(f.native is not None for f in report.features)
    # Viajan en campos separados del JSON, nunca fusionadas en una sola cifra.
    payload = report.to_dict()["features"][0]
    assert "native" in payload and "current" in payload
    # Y para la feature que decide la etiqueta, las dos lecturas no coinciden:
    # el modelo la usa en una fracción de sus cortes, pero perderla cuesta mucho
    # más que esa fracción.
    signal = report.features[0]
    assert signal.native != signal.current


def test_features_without_names_get_stable_positional_ones() -> None:
    x, y = _dataset()
    report = _tracker().measure(_fitted_model(x, y), x, y)
    assert {f.feature for f in report.features} == {"f0", "f1", "f2"}


def test_the_same_model_measured_twice_is_reproducible() -> None:
    # Sin semilla fija, el "cambio" mediría el ruido del método.
    x, y = _dataset()
    model = _fitted_model(x, y)
    first = _tracker().measure(model, x, y, ["a", "b", "c"])
    second = _tracker().measure(model, x, y, ["a", "b", "c"])
    assert [f.current for f in first.features] == [f.current for f in second.features]

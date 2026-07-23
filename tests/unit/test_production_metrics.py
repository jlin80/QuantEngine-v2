"""Registro de métricas: formato de exposición correcto y semántica sana."""

import pytest
from app.production.metrics import Counter, Gauge, Histogram, MetricRegistry


def test_counter_renders_help_type_and_value():
    registry = MetricRegistry()
    registry.counter("qe_things_total", "Cosas contadas.").inc()
    payload = registry.render()
    assert "# HELP qe_things_total Cosas contadas." in payload
    assert "# TYPE qe_things_total counter" in payload
    assert "qe_things_total 1.0" in payload
    assert payload.endswith("\n")  # el formato lo exige


def test_counter_rejects_negative_increments():
    with pytest.raises(ValueError):
        Counter("qe_x_total", "x").inc(-1)


def test_labels_are_rendered_and_sorted():
    gauge = Gauge("qe_positions", "Posiciones.")
    gauge.set(2.0, {"symbol": "BTCUSDT", "side": "long"})
    line = gauge.render()[0]
    # Orden determinista: si no, cada scrape produciría un texto distinto.
    assert line == 'qe_positions{side="long",symbol="BTCUSDT"} 2.0'


def test_label_values_are_escaped():
    gauge = Gauge("qe_thing", "Cosa.")
    gauge.set(1.0, {"name": 'raro"con\\comillas'})
    assert r'name="raro\"con\\comillas"' in gauge.render()[0]


def test_invalid_names_are_rejected():
    with pytest.raises(ValueError):
        Gauge("qe-invalid-name", "x")
    with pytest.raises(ValueError):
        Gauge("qe_ok", "x").set(1.0, {"etiqueta-mala": "v"})


def test_gauge_clear_drops_stale_series():
    """Sin clear, una serie que desaparece se congela en su último valor."""
    gauge = Gauge("qe_component_up", "Componentes.")
    gauge.set(1.0, {"component": "viejo"})
    gauge.clear()
    gauge.set(1.0, {"component": "nuevo"})
    rendered = "\n".join(gauge.render())
    assert "viejo" not in rendered
    assert "nuevo" in rendered


def test_histogram_buckets_are_cumulative():
    histogram = Histogram("qe_latency_seconds", "Latencia.", buckets=(0.1, 1.0))
    for value in (0.05, 0.5, 5.0):
        histogram.observe(value)
    rendered = "\n".join(histogram.render())
    assert 'qe_latency_seconds_bucket{le="0.1"} 1' in rendered
    assert 'qe_latency_seconds_bucket{le="1.0"} 2' in rendered
    assert 'qe_latency_seconds_bucket{le="+Inf"} 3' in rendered
    assert "qe_latency_seconds_count 3" in rendered
    assert "qe_latency_seconds_sum 5.55" in rendered


def test_same_name_with_different_type_is_rejected():
    registry = MetricRegistry()
    registry.counter("qe_dup", "x")
    with pytest.raises(ValueError):
        registry.gauge("qe_dup", "x")


def test_get_or_create_returns_the_same_instance():
    registry = MetricRegistry()
    assert registry.counter("qe_a_total", "x") is registry.counter("qe_a_total", "x")


def test_metrics_without_samples_are_not_exposed():
    """Exponer una métrica declarada pero nunca observada sólo añade ruido."""
    registry = MetricRegistry()
    registry.counter("qe_never_used_total", "x")
    assert "qe_never_used_total" not in registry.render()


def test_empty_registry_renders_valid_payload():
    assert MetricRegistry().render() == "\n"

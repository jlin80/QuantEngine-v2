"""Meta Risk Engine (Bloque 12): salud de la máquina y composición del riesgo."""

import pytest
from app.config.settings import MetaRiskSettings
from app.monitoring.meta_risk import MetaRiskEngine, MetaRiskInputs


def _engine(inputs: MetaRiskInputs, **overrides) -> MetaRiskEngine:
    return MetaRiskEngine(MetaRiskSettings(**overrides), lambda: inputs)


def _healthy(**overrides) -> MetaRiskInputs:
    defaults = {
        "cpu_percent": 20.0,
        "memory_percent": 30.0,
        "event_loop_lag_ms": 5.0,
        "event_bus_queue": 10,
        "component_statuses": {
            "redis": "healthy",
            "broker": "healthy",
            "mt5": "healthy",
            "exchange": "healthy",
            "api": "healthy",
            "scheduler": "healthy",
            "cache": "healthy",
        },
        "data_quality_multiplier": 1.0,
    }
    return MetaRiskInputs(**{**defaults, **overrides})


def test_a_healthy_machine_does_not_touch_exposure() -> None:
    report = _engine(_healthy()).measure()
    assert report.risk_multiplier == 1.0
    assert report.degraded is False
    assert report.score is not None and report.score > 95.0


def test_a_saturated_cpu_shrinks_exposure() -> None:
    # Una VPS al 95% no impide operar: impide operar a tiempo, y eso se lee
    # como slippage y salidas tardías, no como problema de máquina.
    report = _engine(_healthy(cpu_percent=95.0, memory_percent=95.0)).measure()
    assert report.degraded is True
    assert report.risk_multiplier < 1.0


def test_a_broker_down_degrades_on_its_own() -> None:
    statuses = dict(_healthy().component_statuses)
    statuses["broker"] = "down"
    report = _engine(_healthy(component_statuses=statuses)).measure()
    assert report.degraded is True
    assert any("broker" in reason for reason in report.reasons)
    assert report.risk_multiplier <= 0.5


def test_a_component_that_does_not_report_is_not_a_component_that_is_down() -> None:
    # Redis en local, MT5 en cripto: penalizarlos apagaría medio sistema por
    # configuración, no por avería.
    report = _engine(_healthy(component_statuses={"broker": "healthy"})).measure()
    assert "redis" in report.missing
    assert "mt5" in report.missing
    assert report.risk_multiplier == 1.0


def test_a_degraded_component_scores_between_healthy_and_down() -> None:
    def _with(status: str) -> float:
        statuses = dict(_healthy().component_statuses)
        statuses["exchange"] = status
        return _engine(_healthy(component_statuses=statuses)).measure().signals["exchange"]

    assert _with("down") < _with("degraded") < _with("healthy")


def test_data_quality_and_infrastructure_compose_by_product() -> None:
    # Un feed mediocre en una máquina saturada es peor que cualquiera de las dos
    # cosas por separado; quedarse con el mínimo lo negaría.
    only_data = _engine(_healthy(data_quality_multiplier=0.5)).measure().risk_multiplier
    only_infra = _engine(_healthy(cpu_percent=95.0)).measure().risk_multiplier
    both = (
        _engine(_healthy(cpu_percent=95.0, data_quality_multiplier=0.5), risk_floor=0.05)
        .measure()
        .risk_multiplier
    )
    assert both < min(only_data, only_infra)


def test_bad_data_quality_alone_is_enough_to_degrade() -> None:
    report = _engine(_healthy(data_quality_multiplier=0.4)).measure()
    assert report.degraded is True
    assert report.risk_multiplier == pytest.approx(0.4, abs=0.01)


def test_the_floor_survives_the_composition() -> None:
    # Componer nunca puede llevar la exposición por debajo de lo permitido.
    report = _engine(
        _healthy(cpu_percent=200.0, event_loop_lag_ms=10_000.0, data_quality_multiplier=0.05),
        risk_floor=0.25,
    ).measure()
    assert report.risk_multiplier == pytest.approx(0.25)


def test_an_event_loop_stall_degrades_on_its_own() -> None:
    report = _engine(_healthy(event_loop_lag_ms=5_000.0)).measure()
    assert report.degraded is True
    assert any("event_loop" in reason for reason in report.reasons)


def test_a_flooded_event_bus_lowers_its_signal() -> None:
    report = _engine(_healthy(event_bus_queue=9_500), max_event_bus_queue=10_000).measure()
    assert report.signals["event_bus"] < 0.2


def test_without_any_signal_risk_is_left_alone() -> None:
    engine = MetaRiskEngine(MetaRiskSettings(weights={}), lambda: MetaRiskInputs())
    report = engine.measure()
    assert report.risk_multiplier == 1.0


def test_the_multiplier_is_one_until_something_has_been_measured() -> None:
    assert _engine(_healthy()).risk_multiplier() == 1.0

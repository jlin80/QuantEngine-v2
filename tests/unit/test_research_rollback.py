"""Umbrales que apagan el ciclo autónomo si el motor operativo se degrada.

El presupuesto del Bloque 6 decide si el ciclo puede *arrancar*; esto decide si
hay que *apagarlo*. La asimetría es deliberada y se fija aquí: sólo apaga el
laboratorio, nunca toca la operativa, y no se rearma solo.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import ResearchRollbackSettings, ResearchSettings, Settings
from app.research.rollback import (
    CLOCK_SKEW,
    CPU,
    MANAGE_LATENCY,
    PIPELINE_ALARM,
    ResearchRollbackMonitor,
)
from app.utils.time import use_clock


def _monitor(**overrides) -> ResearchRollbackMonitor:
    return ResearchRollbackMonitor(settings=ResearchRollbackSettings(**overrides))


def _names(triggers) -> set[str]:
    return {t.name for t in triggers}


# ----------------------------------------------------------------------
# El caso base: un motor sano no apaga nada
# ----------------------------------------------------------------------


def test_a_healthy_engine_never_rolls_back():
    monitor = _monitor()

    triggers = monitor.evaluate(
        cpu_pct=40.0,
        manage_ema_seconds=0.10,
        baseline_latency_seconds=0.09,
        manage_passes=100,
    )

    assert triggers == ()


# ----------------------------------------------------------------------
# CPU: sostenida, no un pico
# ----------------------------------------------------------------------


def test_a_single_cpu_spike_does_not_roll_back():
    """Un pico durante un ciclo de research es lo esperado, no una degradación.

    Disparar con el primero haría la vigilancia inútil: se apagaría a sí misma
    en el primer ciclo que hiciera su trabajo.
    """
    monitor = _monitor(cpu_breaches_before_rollback=3)

    assert monitor.evaluate(cpu_pct=99.0) == ()


def test_sustained_cpu_rolls_back():
    monitor = _monitor(max_cpu_pct=85.0, cpu_breaches_before_rollback=3)

    monitor.evaluate(cpu_pct=99.0)
    monitor.evaluate(cpu_pct=99.0)
    triggers = monitor.evaluate(cpu_pct=99.0)

    assert CPU in _names(triggers)
    assert "3 muestras" in triggers[0].detail


def test_a_recovered_cpu_resets_the_streak():
    """Dos picos separados por normalidad no suman: no es una racha."""
    monitor = _monitor(cpu_breaches_before_rollback=3)

    monitor.evaluate(cpu_pct=99.0)
    monitor.evaluate(cpu_pct=99.0)
    monitor.evaluate(cpu_pct=30.0)
    triggers = monitor.evaluate(cpu_pct=99.0)

    assert triggers == ()


def test_a_mute_cpu_sensor_never_rolls_back():
    """Misma regla que el presupuesto y que Safe Mode: sin lectura no se bloquea."""
    monitor = _monitor(cpu_breaches_before_rollback=1)

    assert monitor.evaluate(cpu_pct=None) == ()


# ----------------------------------------------------------------------
# Latencia del bucle de gestión: el único que no puede llegar tarde
# ----------------------------------------------------------------------


def test_a_slow_management_loop_rolls_back():
    monitor = _monitor(max_manage_latency_ratio=2.0, min_manage_passes=30)

    triggers = monitor.evaluate(
        manage_ema_seconds=0.50,
        baseline_latency_seconds=0.10,
        manage_passes=100,
    )

    assert MANAGE_LATENCY in _names(triggers)
    assert "5.0×" in triggers[0].detail


def test_latency_is_judged_against_its_own_baseline_not_an_absolute():
    """Lo que importa es la degradación relativa, no los milisegundos.

    Un bucle lento pero establemente lento no es una degradación causada por el
    research; un bucle que se duplica sí lo es, aunque siga siendo rápido.
    """
    monitor = _monitor(max_manage_latency_ratio=2.0, min_manage_passes=1)

    slow_but_stable = monitor.evaluate(
        manage_ema_seconds=2.0, baseline_latency_seconds=1.8, manage_passes=100
    )

    assert slow_but_stable == ()


def test_with_too_few_passes_latency_is_not_judged():
    """Comparar contra una línea base que no existe fabrica falsos positivos."""
    monitor = _monitor(min_manage_passes=30)

    triggers = monitor.evaluate(
        manage_ema_seconds=10.0,
        baseline_latency_seconds=0.1,
        manage_passes=5,
    )

    assert triggers == ()


def test_without_a_baseline_latency_is_not_judged():
    monitor = _monitor(min_manage_passes=1)

    triggers = monitor.evaluate(
        manage_ema_seconds=10.0, baseline_latency_seconds=None, manage_passes=100
    )

    assert triggers == ()


# ----------------------------------------------------------------------
# Reloj y alarmas de pipeline
# ----------------------------------------------------------------------


def test_a_leaked_clock_rolls_back():
    """La causa exacta del incidente, vigilada también desde aquí."""
    frozen = datetime.now(UTC) - timedelta(days=4)
    monitor = _monitor(max_clock_skew_seconds=5.0)

    with use_clock(lambda: frozen):
        triggers = monitor.evaluate()

    assert CLOCK_SKEW in _names(triggers)


def test_a_blind_engine_rolls_back_the_lab():
    """Con el motor sin operar, el laboratorio no tiene ninguna prioridad.

    No hace falta demostrar que el research causó la ceguera: apagarlo no cuesta
    nada y puede ser justo lo que hacía falta.
    """
    monitor = _monitor()
    monitor.on_pipeline_alarm("market_data_blind")

    triggers = monitor.evaluate()

    assert PIPELINE_ALARM in _names(triggers)


def test_a_recovered_alarm_stops_triggering():
    monitor = _monitor()
    monitor.on_pipeline_alarm("market_data_blind")
    monitor.on_pipeline_recovered("market_data_blind")

    assert monitor.evaluate() == ()


def test_every_trigger_carries_numbers():
    """Un aviso sin cifras no es accionable a las 3 de la mañana."""
    monitor = _monitor(cpu_breaches_before_rollback=1)

    triggers = monitor.evaluate(
        cpu_pct=99.0,
        manage_ema_seconds=1.0,
        baseline_latency_seconds=0.1,
        manage_passes=100,
    )

    assert len(triggers) == 2
    assert all(any(ch.isdigit() for ch in t.detail) for t in triggers)


# ----------------------------------------------------------------------
# El punto 3 del bloque: no se activa nada
# ----------------------------------------------------------------------


def test_auto_cycle_is_still_off_by_default():
    """El toggle queda listo; activarlo es una decisión del operador, no del código."""
    assert ResearchSettings().auto_cycle is False
    assert Settings().research.auto_cycle is False


def test_the_rollback_watch_is_on_by_default():
    """Al revés que el ciclo: la salvaguarda no debería requerir activarla."""
    assert ResearchRollbackSettings().enabled is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_cpu_pct", 85.0),
        ("cpu_breaches_before_rollback", 3),
        ("max_manage_latency_ratio", 2.0),
        ("min_manage_passes", 30),
        ("max_clock_skew_seconds", 5.0),
    ],
)
def test_the_documented_thresholds_are_the_configured_ones(field, value):
    """Si los umbrales del plan y los del código divergen, el plan no vale."""
    assert getattr(ResearchRollbackSettings(), field) == value

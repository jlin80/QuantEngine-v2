"""Ventana semanal de entrenamiento del ML.

El entrenamiento compite por CPU con el motor que opera, así que se lleva al
fin de semana. Lo que se prueba aquí es la política de calendario, con reloj
falso: cuándo dispara, cuándo no, y que no se dispare 24 veces el mismo sábado.
"""

from datetime import UTC, datetime, timedelta

from app.config.settings import MLTrainingSettings
from app.ml.schedule import WeeklyTrainingGate

# 2026-08-15 es sábado; 2026-08-12, miércoles.
SATURDAY = datetime(2026, 8, 15, 7, 0, tzinfo=UTC)
WEDNESDAY = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)


def _settings(tmp_path, **over) -> MLTrainingSettings:
    return MLTrainingSettings(
        weekly_enabled=True,
        weekly_weekday=5,
        weekly_hour_utc=6,
        weekly_window_hours=12,
        schedule_state_path=tmp_path / "training_schedule.json",
        **over,
    )


def test_runs_inside_the_weekend_window(tmp_path):
    gate = WeeklyTrainingGate(_settings(tmp_path))
    verdict = gate.decide(SATURDAY)
    assert verdict["run"] is True
    assert verdict["kind"] == "scheduled"


def test_does_not_run_midweek(tmp_path):
    """Es justo lo que el cambio viene a evitar: entrenar con mercado abierto."""
    gate = WeeklyTrainingGate(_settings(tmp_path))
    assert gate.decide(WEDNESDAY)["run"] is False


def test_does_not_run_before_the_window_opens(tmp_path):
    gate = WeeklyTrainingGate(_settings(tmp_path))
    # Sábado a las 05:00, con la ventana abriendo a las 06:00.
    assert gate.decide(SATURDAY.replace(hour=5))["run"] is False


def test_does_not_run_after_the_window_closes(tmp_path):
    gate = WeeklyTrainingGate(_settings(tmp_path))
    # Ventana 06:00–18:00; a las 19:00 ya cerró.
    assert gate.decide(SATURDAY.replace(hour=19))["run"] is False


def test_runs_only_once_per_window(tmp_path):
    """El job tiquea cada 30 min: sin esto entrenaría 24 veces cada sábado."""
    gate = WeeklyTrainingGate(_settings(tmp_path))
    assert gate.decide(SATURDAY)["run"] is True
    gate.mark_ran(SATURDAY)
    assert gate.decide(SATURDAY + timedelta(minutes=30))["run"] is False
    assert gate.decide(SATURDAY + timedelta(hours=4))["run"] is False


def test_runs_again_the_following_week(tmp_path):
    gate = WeeklyTrainingGate(_settings(tmp_path))
    gate.mark_ran(SATURDAY)
    assert gate.decide(SATURDAY + timedelta(days=7))["run"] is True


def test_survives_a_restart(tmp_path):
    """El scheduler reinicia su reloj al arrancar; el estado vive en disco.

    Sin persistencia, cada reinicio dentro de la ventana relanzaría el
    entrenamiento — y hoy la VPS se reinicia varias veces al día.
    """
    settings = _settings(tmp_path)
    first = WeeklyTrainingGate(settings)
    first.mark_ran(SATURDAY)

    restarted = WeeklyTrainingGate(settings)
    assert restarted.last_run == SATURDAY
    assert restarted.decide(SATURDAY + timedelta(hours=2))["run"] is False


def test_first_ever_start_waits_for_the_window(tmp_path):
    """Sin estado previo y fuera de ventana, NO se entrena al arrancar."""
    gate = WeeklyTrainingGate(_settings(tmp_path))
    verdict = gate.decide(WEDNESDAY)
    assert verdict["run"] is False
    assert "espera" in verdict["reason"]


def test_catches_up_after_a_long_outage(tmp_path):
    """Si el motor estuvo caído todo el fin de semana, no se pierde la semana.

    Sin esta red, un patrón de caídas los sábados dejaría el modelo sin
    reentrenar indefinidamente y nada lo delataría.
    """
    gate = WeeklyTrainingGate(_settings(tmp_path, weekly_max_staleness_days=10.0))
    gate.mark_ran(SATURDAY)
    verdict = gate.decide(SATURDAY + timedelta(days=11))
    assert verdict["run"] is True
    assert verdict["kind"] == "catchup"


def test_does_not_catch_up_before_the_staleness_margin(tmp_path):
    gate = WeeklyTrainingGate(_settings(tmp_path, weekly_max_staleness_days=10.0))
    gate.mark_ran(SATURDAY)
    assert gate.decide(SATURDAY + timedelta(days=3))["run"] is False


def test_unreadable_state_does_not_block_training(tmp_path):
    """Un estado corrupto no puede dejar el ML sin entrenar para siempre."""
    path = tmp_path / "training_schedule.json"
    path.write_text("{ esto no es json", encoding="utf-8")
    gate = WeeklyTrainingGate(_settings(tmp_path))
    assert gate.last_run is None
    assert gate.decide(SATURDAY)["run"] is True


def test_reason_is_always_reported(tmp_path):
    """Un job que se salta en silencio es indistinguible de uno roto."""
    gate = WeeklyTrainingGate(_settings(tmp_path))
    for moment in (SATURDAY, WEDNESDAY, SATURDAY.replace(hour=3)):
        assert gate.decide(moment)["reason"]

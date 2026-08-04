"""El motor no arranca con instrumentación de test o backtest activa.

Tercera pata del incidente del reloj: ADR-091 impide la fuga, ADR-093 vigila el
efecto, y esto comprueba **al arrancar** que el proceso está limpio. La regla
que gobierna el módulo es que arrancar en silencio con estado contaminado es
peor que no arrancar: un motor caído se ve en el primer minuto, uno ciego tardó
cuatro días.
"""

import sys
from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import Environment, Settings
from app.engine.startup_guard import (
    ContaminatedStartupError,
    inspect_startup,
    verify_clean_startup,
)
from app.utils.time import use_clock


def _settings(environment: Environment) -> Settings:
    return Settings(environment=environment)


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


# ----------------------------------------------------------------------
# El caso que importa tanto como los fallos: un arranque limpio arranca
# ----------------------------------------------------------------------


def test_a_clean_startup_is_not_blocked(monkeypatch):
    """Un guard que bloquea de más se acaba desactivando, y entonces no protege."""
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delitem(sys.modules, "app.backtesting.quant_source", raising=False)

    report = verify_clean_startup(_settings(Environment.PAPER))

    assert report.clean
    assert report.enforced


# ----------------------------------------------------------------------
# Reloj contaminado — la causa exacta del incidente
# ----------------------------------------------------------------------


def test_a_leaked_simulated_clock_aborts_the_startup():
    """Al arrancar no hay motivo legítimo para tener un reloj inyectado."""
    frozen = datetime.now(UTC) - timedelta(days=4)

    with (
        use_clock(lambda: frozen),
        pytest.raises(ContaminatedStartupError, match="reloj simulado"),
    ):
        verify_clean_startup(_settings(Environment.PAPER))


def test_a_clock_within_tolerance_passes():
    """Una desviación por debajo del umbral de salud no es contaminación."""
    almost_now = datetime.now(UTC) + timedelta(seconds=1)

    with use_clock(lambda: almost_now):
        report = inspect_startup(_settings(Environment.PAPER))

    assert _check(report, "clock_skew").passed


# ----------------------------------------------------------------------
# Instrumentación de test viva en un proceso que opera
# ----------------------------------------------------------------------


def test_test_instrumentation_aborts_the_startup():
    """Los monkeypatch de un test no se deshacen fuera de él.

    Esta suite corre bajo pytest, así que la condición se cumple sola: si el
    guard no la detectase, este test no tendría nada que comprobar.
    """
    with pytest.raises(ContaminatedStartupError, match="instrumentación de test"):
        verify_clean_startup(_settings(Environment.PRODUCTION))


def test_the_current_test_variable_is_enough_on_its_own(monkeypatch):
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "algun_test")

    report = inspect_startup(_settings(Environment.PAPER))

    assert not _check(report, "test_instrumentation").passed


# ----------------------------------------------------------------------
# Un backtest que ya corrió en este proceso
# ----------------------------------------------------------------------


def test_a_backtest_event_loop_left_running_aborts_the_startup(monkeypatch):
    """Encontrarlo antes del arranque significa que un backtest corrió primero.

    Es la mezcla proceso-motor / proceso-backtest que el incidente demostró que
    no es segura, detectada por su rastro más visible.
    """
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    import app.backtesting.quant_source as quant_source

    monkeypatch.setattr(quant_source, "_LOOP", object(), raising=False)

    with pytest.raises(ContaminatedStartupError, match="backtesting"):
        verify_clean_startup(_settings(Environment.PAPER))


def test_a_backtesting_module_never_imported_is_not_contamination(monkeypatch):
    monkeypatch.delitem(sys.modules, "app.backtesting.quant_source", raising=False)

    report = inspect_startup(_settings(Environment.PAPER))

    assert _check(report, "backtest_loop").passed


# ----------------------------------------------------------------------
# Alcance: dónde se aplica y dónde no
# ----------------------------------------------------------------------


@pytest.mark.parametrize("environment", [Environment.DEVELOPMENT, Environment.TESTING])
def test_development_and_testing_report_but_never_abort(environment):
    """Allí la instrumentación de test es lo normal; abortar sería un estorbo.

    Un guard que estorba se desactiva, y un guard desactivado no protege — así
    que se limita a los entornos donde la contaminación es de verdad un fallo.
    """
    report = verify_clean_startup(_settings(environment))

    assert not report.enforced
    assert not report.clean  # la suite corre bajo pytest: lo ve, y no aborta


def test_the_report_says_what_failed_and_why():
    """Un aborto que no explica qué encontró obliga a adivinar en producción."""
    report = inspect_startup(_settings(Environment.PAPER))

    payload = report.to_dict()
    assert payload["environment"] == "paper"
    assert payload["enforced"] is True
    assert {c["name"] for c in payload["checks"]} == {
        "clock_skew",
        "test_instrumentation",
        "backtest_loop",
    }
    assert all(c["detail"] for c in payload["checks"] if not c["passed"])

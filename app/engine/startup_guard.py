"""Fail-fast: el motor no arranca con instrumentación de test o backtest activa.

El incidente del 2026-07-31 no fue sólo un reloj mal guardado. Fue que el motor
**arrancó y siguió operando** con el reloj de un backtest instalado, cuatro días,
sin que nada lo notase: el proceso respondía, los servicios figuraban `running`
y el watchdog no tenía nada que decir. ADR-091 impide esa fuga concreta y
ADR-093 vigila el efecto; falta la tercera pata — comprobar **al arrancar** que
el proceso está limpio, y **abortar** si no lo está.

La decisión que gobierna este módulo: *arrancar en silencio con estado
contaminado es peor que no arrancar*. Un motor que no arranca se ve en el primer
minuto. Un motor ciego tardó cuatro días.

Por eso el guard sólo aplica en los entornos donde eso importa (`paper` y
`production`): en `development`/`testing` la instrumentación de test es lo
normal, y abortar allí convertiría al guard en un estorbo que alguien acabaría
desactivando — que es como se pierden las salvaguardas.
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from app.config.environment import Environment
from app.config.settings import Settings
from app.utils.time import clock_skew_seconds

_LOG = logging.getLogger("app.engine.startup_guard")

# Entornos donde una contaminación de test/backtest es un fallo, no lo esperado.
_GUARDED = (Environment.PAPER, Environment.PRODUCTION)


class ContaminatedStartupError(RuntimeError):
    """El proceso arrancó con instrumentación de test o backtest activa.

    Es deliberadamente fatal. Degradar a warning devolvería exactamente el modo
    de fallo que el incidente del reloj demostró: operar mal, en silencio.
    """


@dataclass(frozen=True, kw_only=True, slots=True)
class StartupCheck:
    """Resultado de una comprobación de arranque.

    Attributes:
        name: Identificador de la comprobación.
        passed: Si el proceso está limpio en ese aspecto.
        detail: Qué se encontró (vacío si pasó).
    """

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class StartupReport:
    """Conjunto de comprobaciones ejecutadas al arrancar.

    Attributes:
        environment: Entorno evaluado.
        enforced: Si un fallo aborta el arranque en este entorno.
        checks: Comprobaciones, en orden de ejecución.
    """

    environment: str
    enforced: bool
    checks: tuple[StartupCheck, ...] = field(default_factory=tuple)

    @property
    def failures(self) -> tuple[StartupCheck, ...]:
        """Comprobaciones que encontraron contaminación."""
        return tuple(c for c in self.checks if not c.passed)

    @property
    def clean(self) -> bool:
        """Whether every check passed."""
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (diagnóstico y auditoría)."""
        return {
            "environment": self.environment,
            "enforced": self.enforced,
            "clean": self.clean,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
        }


def _check_clock_skew(max_skew_seconds: float) -> StartupCheck:
    """El reloj efectivo no puede diferir del de pared al arrancar.

    Es la comprobación directa del incidente. Al arrancar no hay ningún motivo
    legítimo para tener un reloj inyectado: el backtesting instala el suyo
    dentro de su propia tarea, mucho después.
    """
    skew = abs(clock_skew_seconds())
    if skew <= max_skew_seconds:
        return StartupCheck(name="clock_skew", passed=True)
    return StartupCheck(
        name="clock_skew",
        passed=False,
        detail=(
            f"El reloj efectivo difiere {skew:.1f}s del reloj de pared "
            f"(máximo {max_skew_seconds:.1f}s). Hay un reloj simulado instalado "
            f"en el contexto de arranque."
        ),
    )


def _check_test_instrumentation() -> StartupCheck:
    """`pytest` cargado en un proceso que opera es contaminación, no un detalle.

    Se mira ``sys.modules`` y ``PYTEST_CURRENT_TEST``: el primero detecta un
    proceso arrancado desde la suite, el segundo un test en curso. Los
    monkeypatch de un test no se pueden enumerar, pero sí se puede detectar el
    entorno que los produce — que es lo que importa.
    """
    markers: list[str] = []
    if "pytest" in sys.modules:
        markers.append("el módulo 'pytest' está cargado")
    if os.environ.get("PYTEST_CURRENT_TEST"):
        markers.append("PYTEST_CURRENT_TEST está definida")
    if not markers:
        return StartupCheck(name="test_instrumentation", passed=True)
    return StartupCheck(
        name="test_instrumentation",
        passed=False,
        detail=(
            f"El proceso lleva instrumentación de test ({', '.join(markers)}). "
            f"Los monkeypatch de un test no se deshacen fuera de él."
        ),
    )


def _check_backtest_loop() -> StartupCheck:
    """Un backtest que ya corrió en este proceso deja su event loop de fondo.

    ``app.backtesting.quant_source`` arranca un loop en un hilo daemon que nunca
    se cierra. Encontrarlo **antes** de que el motor arranque significa que un
    backtest se ejecutó primero en este mismo proceso — exactamente la mezcla
    que el incidente demostró que no es segura.

    Se importa de forma diferida y se lee el atributo por nombre: el guard no
    debe arrastrar la capa de backtesting al arranque del motor sólo para
    mirarla.
    """
    module = sys.modules.get("app.backtesting.quant_source")
    if module is None or getattr(module, "_LOOP", None) is None:
        return StartupCheck(name="backtest_loop", passed=True)
    return StartupCheck(
        name="backtest_loop",
        passed=False,
        detail=(
            "Hay un event loop de backtesting activo antes de arrancar el motor: "
            "un backtest ya corrió en este proceso."
        ),
    )


def _check_runtime_config() -> StartupCheck:
    """La configuración del operador tiene que haberse podido leer.

    El 13/08 un BOM al principio del JSON rompió el ``json.loads`` del store, su
    ``except`` descartó el fichero **entero** y el motor arrancó con cero
    overrides: sin el freno de pérdida diaria, sin los topes por símbolo y con
    las estrategias apagadas de vuelta a activas. Nada lo dijo — desde fuera,
    arrancar sin configuración es idéntico a arrancar bien.

    Igual que el reloj: *arrancar en silencio con configuración perdida es peor
    que no arrancar*. Un motor caído se ve en el primer minuto; uno operando con
    los frenos quitados, no.

    Import diferido para no arrastrar la capa del dashboard al arranque del
    motor sólo para mirarla, mismo criterio que ``_check_backtest_loop``.
    """
    from app.dashboard.api.config_store import config_store

    if config_store.load_error is None:
        return StartupCheck(name="runtime_config", passed=True)
    return StartupCheck(
        name="runtime_config",
        passed=False,
        detail=(
            f"La configuración del operador no se pudo leer ({config_store.load_error}). "
            f"Arrancar aplicaría los valores por defecto y se perderían los límites "
            f"de riesgo configurados. Repara o retira el fichero antes de arrancar."
        ),
    )


def inspect_startup(settings: Settings) -> StartupReport:
    """Run every startup check without raising.

    Args:
        settings: Configuración raíz (entorno y umbral de desviación).

    Returns:
        El informe completo, incluso en entornos donde no se aplica.
    """
    return StartupReport(
        environment=settings.environment.value,
        enforced=settings.environment in _GUARDED,
        checks=(
            _check_clock_skew(settings.health.max_clock_skew_seconds),
            _check_test_instrumentation(),
            _check_backtest_loop(),
            _check_runtime_config(),
        ),
    )


def verify_clean_startup(settings: Settings) -> StartupReport:
    """Abort the startup if the process carries test/backtest instrumentation.

    Args:
        settings: Configuración raíz.

    Returns:
        El informe de comprobaciones (limpio, o sucio en entorno no vigilado).

    Raises:
        ContaminatedStartupError: Si alguna comprobación falla en `paper` o
            `production`.
    """
    report = inspect_startup(settings)
    if report.clean:
        _LOG.info("Arranque limpio: %d comprobaciones superadas", len(report.checks))
        return report

    detail = "; ".join(f"{c.name}: {c.detail}" for c in report.failures)
    if not report.enforced:
        # En desarrollo y en la propia suite esto es lo normal, no un fallo.
        _LOG.debug("Comprobaciones de arranque no superadas (entorno no vigilado): %s", detail)
        return report

    _LOG.critical("Arranque abortado por estado contaminado — %s", detail)
    raise ContaminatedStartupError(
        f"El motor no arranca en '{report.environment}' con estado contaminado. {detail}"
    )

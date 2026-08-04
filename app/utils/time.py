"""Helpers de tiempo. Todo el sistema trabaja en UTC, sin excepciones.

El reloj es inyectable: :func:`utc_now` lee de un proveedor que por defecto es
el reloj de pared. El laboratorio de backtesting (Fase 6) instala un reloj
simulado con :func:`use_clock` para que el tiempo en mercado, las salidas por
tiempo y los timestamps de las operaciones reflejen el momento histórico
reproducido — sin duplicar el motor de ejecución.

**El proveedor vive en un ``ContextVar``, no en un global de módulo.** La razón
no es estilística: el ``BacktestLab`` corre en el *mismo proceso y el mismo
event loop* que el motor en vivo. Con un global, instalar el reloj de replay
congelaba la hora de **todo el proceso** mientras durase el backtest — y si el
bloque no llegaba a cerrarse (tarea cancelada o colgada), la congelaba para
siempre.

Eso ocurrió en producción: el 2026-07-31 a las 16:41 UTC un backtest dejó el
reloj instalado y el motor vivió 4 días creyendo que era el 31 de julio. El
validador de mercado comparaba cada tick del broker contra esa hora, los veía
todos "en el futuro" y **descartaba el 100%**: sin velas, sin señales, sin
operaciones. Ninguna alarma saltó porque el motor no estaba caído, sólo ciego.

Con ``ContextVar`` el reloj simulado sólo alcanza a la tarea que lo instala y a
las que ella cree — que es exactamente el alcance de un backtest. Las tareas del
motor en vivo, creadas en otro contexto, siguen viendo el reloj de pared pase lo
que pase con el bloque.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_clock: ContextVar[Callable[[], datetime] | None] = ContextVar("qe_clock", default=None)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Usa el reloj inyectado en el contexto actual si hay uno (backtesting); si
    no, el reloj de pared del sistema.

    Returns:
        Current UTC time with ``tzinfo`` set.
    """
    clock = _clock.get()
    if clock is not None:
        return clock()
    return datetime.now(UTC)


def wall_now() -> datetime:
    """Return the wall-clock UTC time, ignoring any injected clock.

    Lo usa la vigilancia que compara el reloj del motor con el del sistema: si
    preguntase por :func:`utc_now` no podría detectar nunca una desviación.
    """
    return datetime.now(UTC)


def clock_skew_seconds() -> float:
    """Segundos de desviación entre el reloj efectivo y el de pared.

    ``0.0`` cuando no hay reloj inyectado. Un valor grande en el proceso del
    motor en vivo significa que un reloj simulado se ha filtrado fuera de su
    contexto — el fallo silencioso que este módulo previene.
    """
    clock = _clock.get()
    if clock is None:
        return 0.0
    return (clock() - datetime.now(UTC)).total_seconds()


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """Install (or clear) the time provider for the current context.

    Preferir :func:`use_clock`, que restaura el anterior al salir. Este setter
    existe para configuraciones de prueba que abarcan un test entero.

    Args:
        clock: Callable que devuelve el "ahora" en UTC, o ``None`` para
            volver al reloj de pared.
    """
    _clock.set(clock)


@contextmanager
def use_clock(clock: Callable[[], datetime]) -> Iterator[None]:
    """Temporarily install a time provider, restoring the previous one on exit.

    El alcance es el **contexto actual**: la tarea que abre el bloque y las que
    cree dentro. Las tareas hermanas —el motor en vivo— no se ven afectadas ni
    aunque este bloque no llegue a cerrarse.

    Args:
        clock: Proveedor de tiempo a usar dentro del bloque ``with``.

    Yields:
        Nada; el reloj queda activo durante el bloque.
    """
    token = _clock.set(clock)
    try:
        yield
    finally:
        _clock.reset(token)


def isoformat_utc(moment: datetime | None = None) -> str:
    """Format a datetime (default: now) as an ISO-8601 UTC string.

    Args:
        moment: Datetime to format. Naive datetimes are assumed to be UTC.

    Returns:
        ISO-8601 string with explicit UTC offset.
    """
    if moment is None:
        moment = utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()

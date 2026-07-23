"""Helpers de tiempo. Todo el sistema trabaja en UTC, sin excepciones.

El reloj es inyectable: :func:`utc_now` lee de un proveedor global que por
defecto es el reloj de pared. El laboratorio de backtesting (Fase 6) instala
un reloj simulado con :func:`use_clock` para que el tiempo en mercado, las
salidas por tiempo y los timestamps de las operaciones reflejen el momento
histórico reproducido — sin duplicar el motor de ejecución. En producción el
proveedor es ``None`` y el comportamiento es idéntico al reloj de pared.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

_clock: Callable[[], datetime] | None = None


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Usa el reloj inyectado si hay uno instalado (backtesting); si no, el
    reloj de pared del sistema.

    Returns:
        Current UTC time with ``tzinfo`` set.
    """
    if _clock is not None:
        return _clock()
    return datetime.now(UTC)


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """Install (or clear) the global time provider.

    Args:
        clock: Callable que devuelve el "ahora" en UTC, o ``None`` para
            volver al reloj de pared.
    """
    global _clock
    _clock = clock


@contextmanager
def use_clock(clock: Callable[[], datetime]) -> Iterator[None]:
    """Temporarily install a time provider, restoring the previous one on exit.

    Args:
        clock: Proveedor de tiempo a usar dentro del bloque ``with``.

    Yields:
        Nada; el reloj queda activo durante el bloque.
    """
    global _clock
    previous = _clock
    _clock = clock
    try:
        yield
    finally:
        _clock = previous


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

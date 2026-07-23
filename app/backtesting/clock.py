"""Reloj simulado del laboratorio de backtesting (Fase 6).

El :class:`ReplayClock` mantiene un "ahora" mutable que el motor de ejecución
consume de forma transparente a través del seam de :mod:`app.utils.time`. Así
el tiempo en mercado, las salidas por tiempo y los timestamps de las
operaciones reflejan el momento histórico reproducido, reutilizando el mismo
Execution Engine que el paper trading en vivo.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from app.utils.time import use_clock


class ReplayClock:
    """A controllable UTC clock advanced explicitly by the backtest loop.

    Args:
        start: Momento inicial (UTC). Si es naive se asume UTC.
    """

    def __init__(self, start: datetime) -> None:
        self._now = self._as_utc(start)

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        """Return a timezone-aware UTC datetime."""
        if moment.tzinfo is None:
            return moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)

    def now(self) -> datetime:
        """Current simulated time (UTC)."""
        return self._now

    def set(self, moment: datetime) -> None:
        """Jump the clock to an explicit moment.

        Args:
            moment: Nuevo "ahora" (UTC).
        """
        self._now = self._as_utc(moment)

    @contextmanager
    def installed(self) -> Iterator["ReplayClock"]:
        """Install this clock as the global time provider for a block.

        Yields:
            El propio reloj, ya activo como fuente de :func:`utc_now`.
        """
        with use_clock(self.now):
            yield self

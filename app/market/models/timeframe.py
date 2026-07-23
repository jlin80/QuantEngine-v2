"""Marcos temporales soportados y aritmética de buckets (siempre UTC)."""

import enum
from datetime import UTC, datetime, timedelta

_SECONDS: dict[str, int] = {
    "1s": 1,
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "4h": 14_400,
    "1d": 86_400,
    "1w": 604_800,
}


class Timeframe(enum.StrEnum):
    """Intervalos de agregación soportados por el Data Engine."""

    TICK = "tick"
    S1 = "1s"
    S5 = "5s"
    S15 = "15s"
    S30 = "30s"
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"

    @property
    def seconds(self) -> int | None:
        """Duración fija en segundos; ``None`` para TICK y mensual."""
        return _SECONDS.get(self.value)

    def bucket_start(self, moment: datetime) -> datetime:
        """Return the UTC start of the bucket containing ``moment``.

        Args:
            moment: Timezone-aware datetime to locate.

        Returns:
            Start of the enclosing bucket, timezone-aware UTC.

        Raises:
            ValueError: For :attr:`TICK` (ticks no forman buckets).
        """
        if self is Timeframe.TICK:
            raise ValueError("TICK has no bucket")
        moment = moment.astimezone(UTC)
        if self is Timeframe.MN1:
            return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if self is Timeframe.W1:
            # El módulo epoch ancla en jueves; la semana ISO empieza en lunes.
            day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            return day - timedelta(days=day.weekday())
        step = _SECONDS[self.value]
        epoch = int(moment.timestamp())
        return datetime.fromtimestamp(epoch - epoch % step, tz=UTC)

    def bucket_end(self, start: datetime) -> datetime:
        """Return the exclusive end of the bucket that begins at ``start``.

        Args:
            start: Bucket start as produced by :meth:`bucket_start`.

        Returns:
            First instant of the next bucket.

        Raises:
            ValueError: For :attr:`TICK`.
        """
        if self is Timeframe.TICK:
            raise ValueError("TICK has no bucket")
        if self is Timeframe.MN1:
            if start.month == 12:
                return start.replace(year=start.year + 1, month=1)
            return start.replace(month=start.month + 1)
        return start + timedelta(seconds=_SECONDS[self.value])

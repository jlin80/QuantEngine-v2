"""Buffer de errores recientes, consumido por el Health Monitor."""

import logging
import threading
from collections import deque
from dataclasses import dataclass

from app.utils.time import isoformat_utc


@dataclass(frozen=True, slots=True)
class RecentError:
    """Snapshot de un registro de nivel ERROR o superior."""

    timestamp: str
    level: str
    logger: str
    message: str


class RecentErrorsBuffer(logging.Handler):
    """Logging handler that retains the last N error records in memory."""

    def __init__(self, capacity: int = 50) -> None:
        super().__init__(level=logging.ERROR)
        self._errors: deque[RecentError] = deque(maxlen=capacity)
        self._lock_ = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Store a compact snapshot of the record."""
        entry = RecentError(
            timestamp=isoformat_utc(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
        )
        with self._lock_:
            self._errors.append(entry)

    def snapshot(self) -> list[RecentError]:
        """Return the retained errors, oldest first."""
        with self._lock_:
            return list(self._errors)


_buffer: RecentErrorsBuffer | None = None


def install_recent_errors_buffer(capacity: int = 50) -> RecentErrorsBuffer:
    """Attach (once) the buffer to the root logger and return it."""
    global _buffer
    if _buffer is None:
        _buffer = RecentErrorsBuffer(capacity)
        logging.getLogger().addHandler(_buffer)
    return _buffer


def get_recent_errors() -> list[RecentError]:
    """Return recent errors (empty if the buffer was never installed)."""
    if _buffer is None:
        return []
    return _buffer.snapshot()

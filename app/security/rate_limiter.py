"""Rate limiting por token bucket (Fase 9).

Un cubo de tokens por clave (por defecto, IP del cliente). Cada petición
consume un token; los tokens se reponen a ritmo constante. Es O(1), sin
dependencias y thread-safe. Se usa en el middleware de la API para frenar
ráfagas abusivas sin penalizar el tráfico normal.
"""

import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class _Bucket:
    """Token bucket state for one key."""

    tokens: float
    updated_at: float


class RateLimiter:
    """Token-bucket rate limiter keyed by an arbitrary client identifier.

    Args:
        capacity: Maximum tokens (burst size) — the requests allowed per window.
        window_seconds: Time to fully refill the bucket.
    """

    def __init__(self, capacity: int, window_seconds: float) -> None:
        self._capacity = float(max(1, capacity))
        self._refill_per_second = self._capacity / max(0.001, window_seconds)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Consume one token for ``key``; return whether it was allowed.

        Args:
            key: Client identifier (e.g. IP).
            now: Reference epoch seconds (defaults to wall clock).

        Returns:
            ``True`` if under the limit, ``False`` if the bucket is empty.
        """
        moment = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=self._capacity - 1.0, updated_at=moment)
                return True
            elapsed = max(0.0, moment - bucket.updated_at)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
            bucket.updated_at = moment
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    def reset(self, key: str | None = None) -> None:
        """Forget one key's state, or all keys when ``key`` is ``None``."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

"""Backend de cache en memoria (fallback cuando Redis no está disponible)."""

import time


class InMemoryCache:
    """Simple in-process key-value store with TTL support."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}

    @property
    def backend_name(self) -> str:
        """Backend identifier."""
        return "memory"

    def _is_expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and time.monotonic() >= expires_at

    async def get(self, key: str) -> str | None:
        """Return the value for ``key`` or ``None`` if missing/expired."""
        item = self._data.get(key)
        if item is None:
            return None
        value, expires_at = item
        if self._is_expired(expires_at):
            del self._data[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: float | None = None) -> None:
        """Store ``value`` under ``key`` with an optional TTL."""
        expires_at = time.monotonic() + ttl_seconds if ttl_seconds else None
        self._data[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        """Remove ``key`` (no-op if absent)."""
        self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` holds a non-expired value."""
        return await self.get(key) is not None

    async def ping(self) -> bool:
        """Memory backend is always reachable."""
        return True

    async def close(self) -> None:
        """Drop every stored value."""
        self._data.clear()

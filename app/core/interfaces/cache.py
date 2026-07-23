"""Contrato de backends de cache."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class CacheBackend(Protocol):
    """Key-value cache backend (Redis, memoria, etc.)."""

    @property
    def backend_name(self) -> str:
        """Short backend identifier (e.g. ``redis``, ``memory``)."""
        ...

    async def get(self, key: str) -> str | None:
        """Return the value for ``key`` or ``None`` if missing/expired."""
        ...

    async def set(self, key: str, value: str, ttl_seconds: float | None = None) -> None:
        """Store ``value`` under ``key`` with an optional TTL."""
        ...

    async def delete(self, key: str) -> None:
        """Remove ``key`` (no-op if absent)."""
        ...

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` currently holds a value."""
        ...

    async def ping(self) -> bool:
        """Return whether the backend is reachable."""
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...

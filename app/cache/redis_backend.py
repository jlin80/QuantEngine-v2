"""Backend de cache sobre Redis (redis.asyncio)."""

import contextlib

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.exceptions import CacheError


class RedisCache:
    """Redis-backed cache. Every failure raises :class:`CacheError`.

    Args:
        url: Redis connection URL.
        socket_timeout_seconds: Connect/read timeout.
    """

    def __init__(self, url: str, *, socket_timeout_seconds: float = 3.0) -> None:
        self._url = url
        self._timeout = socket_timeout_seconds
        self._client: aioredis.Redis | None = None

    @property
    def backend_name(self) -> str:
        """Backend identifier."""
        return "redis"

    def _get_client(self) -> aioredis.Redis:
        """Lazily build the Redis client (no connection until first use)."""
        if self._client is None:
            self._client = aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_timeout=self._timeout,
                socket_connect_timeout=self._timeout,
            )
        return self._client

    async def get(self, key: str) -> str | None:
        """Return the value for ``key`` or ``None``."""
        try:
            # decode_responses=True garantiza str, pero los stubs de redis-py
            # declaran bytes | str | None; se normaliza para el chequeo estático.
            value = await self._get_client().get(key)
        except (RedisError, OSError) as exc:
            raise CacheError("Redis GET failed", context={"key": key, "error": repr(exc)}) from exc
        return None if value is None else str(value)

    async def set(self, key: str, value: str, ttl_seconds: float | None = None) -> None:
        """Store ``value`` under ``key`` with an optional TTL."""
        try:
            expiry = int(ttl_seconds) if ttl_seconds else None
            await self._get_client().set(key, value, ex=expiry)
        except (RedisError, OSError) as exc:
            raise CacheError("Redis SET failed", context={"key": key, "error": repr(exc)}) from exc

    async def delete(self, key: str) -> None:
        """Remove ``key``."""
        try:
            await self._get_client().delete(key)
        except (RedisError, OSError) as exc:
            raise CacheError(
                "Redis DELETE failed", context={"key": key, "error": repr(exc)}
            ) from exc

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        try:
            count: int = await self._get_client().exists(key)
        except (RedisError, OSError) as exc:
            raise CacheError(
                "Redis EXISTS failed", context={"key": key, "error": repr(exc)}
            ) from exc
        return bool(count)

    async def ping(self) -> bool:
        """Return whether Redis answers PING."""
        try:
            return bool(await self._get_client().ping())
        except (RedisError, OSError):
            return False

    async def close(self) -> None:
        """Close the client connection pool."""
        if self._client is not None:
            with contextlib.suppress(RedisError, OSError):
                await self._client.aclose()
            self._client = None

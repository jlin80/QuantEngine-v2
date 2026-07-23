"""Pruebas de cache: TTL en memoria y degradación automática."""

import asyncio

from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.core.exceptions import CacheError


class _FailingBackend:
    """Backend que siempre falla (simula Redis caído)."""

    @property
    def backend_name(self) -> str:
        return "failing"

    async def get(self, key):
        raise CacheError("down")

    async def set(self, key, value, ttl_seconds=None):
        raise CacheError("down")

    async def delete(self, key):
        raise CacheError("down")

    async def exists(self, key):
        raise CacheError("down")

    async def ping(self):
        return False

    async def close(self):
        pass


async def test_memory_cache_ttl():
    cache = InMemoryCache()
    await cache.set("k", "v", ttl_seconds=0.05)
    assert await cache.get("k") == "v"
    await asyncio.sleep(0.06)
    assert await cache.get("k") is None


async def test_memory_cache_basico():
    cache = InMemoryCache()
    await cache.set("a", "1")
    assert await cache.exists("a")
    await cache.delete("a")
    assert not await cache.exists("a")


async def test_service_degrades_to_fallback():
    service = CacheService(
        primary=_FailingBackend(), fallback=InMemoryCache(), retry_cooldown_seconds=999
    )
    await service.start()
    assert service.degraded, "el ping de arranque debe detectar el primario caído"

    await service.set("k", "v")
    assert await service.get("k") == "v", "la operación debe servirse del fallback"
    assert service.degraded
    await service.stop()


async def test_service_memory_only():
    service = CacheService(primary=None, fallback=InMemoryCache())
    await service.start()
    await service.set("x", "y", ttl_seconds=10)
    assert await service.get("x") == "y"
    assert service.active_backend_name == "memory"
    await service.stop()

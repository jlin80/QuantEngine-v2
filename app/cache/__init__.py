"""Cache: Redis como primario con degradación automática a memoria."""

from app.cache.memory import InMemoryCache
from app.cache.redis_backend import RedisCache
from app.cache.service import CacheService

__all__ = ["CacheService", "InMemoryCache", "RedisCache"]

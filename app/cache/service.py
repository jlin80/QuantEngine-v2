"""Servicio de cache con degradación automática.

Redis es el backend primario; si falla, el servicio conmuta a memoria y
reintenta el primario tras un periodo de enfriamiento. La lógica de negocio
nunca se entera: la interfaz no cambia y ninguna operación lanza por caída
del backend.
"""

import logging
import time

from app.core.exceptions import CacheError
from app.core.interfaces.cache import CacheBackend
from app.core.lifecycle import Service


class CacheService(Service):
    """Cache facade with primary backend + in-memory fallback.

    Args:
        primary: Preferred backend (Redis). ``None`` = memory only.
        fallback: Always-available backend (memory).
        retry_cooldown_seconds: Wait before re-probing a failed primary.
    """

    def __init__(
        self,
        *,
        primary: CacheBackend | None,
        fallback: CacheBackend,
        retry_cooldown_seconds: float = 30.0,
    ) -> None:
        super().__init__("cache")
        self._primary = primary
        self._fallback = fallback
        self._cooldown = retry_cooldown_seconds
        self._degraded = primary is None
        self._degraded_since: float | None = None
        self._log = logging.getLogger("app.cache")

    @property
    def degraded(self) -> bool:
        """Whether the service is running on the fallback backend."""
        return self._degraded

    @property
    def active_backend_name(self) -> str:
        """Name of the backend currently serving requests."""
        backend = self._active_backend()
        return backend.backend_name

    def _active_backend(self) -> CacheBackend:
        """Pick the backend, re-probing the primary after the cooldown."""
        if self._primary is None:
            return self._fallback
        if not self._degraded:
            return self._primary
        if (
            self._degraded_since is not None
            and time.monotonic() - self._degraded_since >= self._cooldown
        ):
            # Optimista: reintentar el primario; si vuelve a fallar, la
            # operación lo devolverá a modo degradado.
            self._log.info("Cooldown elapsed — retrying primary cache backend")
            self._degraded = False
            self._degraded_since = None
            return self._primary
        return self._fallback

    def _mark_degraded(self, error: CacheError) -> None:
        """Switch to fallback and start the cooldown clock."""
        if not self._degraded:
            self._log.warning(
                "Primary cache backend failed — degrading to '%s': %s",
                self._fallback.backend_name,
                error,
            )
        self._degraded = True
        self._degraded_since = time.monotonic()

    async def get(self, key: str) -> str | None:
        """Return the value for ``key`` (never raises on backend failure)."""
        backend = self._active_backend()
        try:
            return await backend.get(key)
        except CacheError as exc:
            self._mark_degraded(exc)
            return await self._fallback.get(key)

    async def set(self, key: str, value: str, ttl_seconds: float | None = None) -> None:
        """Store ``value`` under ``key`` (never raises on backend failure)."""
        backend = self._active_backend()
        try:
            await backend.set(key, value, ttl_seconds)
        except CacheError as exc:
            self._mark_degraded(exc)
            await self._fallback.set(key, value, ttl_seconds)

    async def delete(self, key: str) -> None:
        """Remove ``key`` from the active backend and the fallback."""
        backend = self._active_backend()
        try:
            await backend.delete(key)
        except CacheError as exc:
            self._mark_degraded(exc)
        await self._fallback.delete(key)

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` exists (fallback on failure)."""
        backend = self._active_backend()
        try:
            return await backend.exists(key)
        except CacheError as exc:
            self._mark_degraded(exc)
            return await self._fallback.exists(key)

    async def healthcheck(self) -> bool:
        """Healthy while running; degraded mode still counts as healthy."""
        return self.is_running

    async def _on_start(self) -> None:
        """Probe the primary once so the degraded flag is accurate early."""
        if self._primary is not None and not await self._primary.ping():
            self._mark_degraded(CacheError("Primary cache backend unreachable at startup"))

    async def _on_stop(self) -> None:
        """Close both backends."""
        if self._primary is not None:
            await self._primary.close()
        await self._fallback.close()

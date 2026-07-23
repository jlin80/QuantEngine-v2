"""Middleware de seguridad de la API (Fase 9): rate limiting + cabeceras.

Aplica dos protecciones baratas y sin estado externo a cada respuesta HTTP:

1. **Rate limiting** por IP (token bucket) para frenar ráfagas abusivas. El
   endpoint de métricas y el WebSocket quedan exentos: Prometheus scrapea a
   ritmo alto y por diseño, y el WS es una única conexión larga.
2. **Cabeceras defensivas** (nosniff, no-frame, referrer estricto) en cada
   respuesta.

Un 429 se audita (best effort) para que quede rastro de abusos.
"""

import logging

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import SecuritySettings
from app.production.audit import AuditAction, AuditLog
from app.security.rate_limiter import RateLimiter

_log = logging.getLogger("app.security")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Permitted-Cross-Domain-Policies": "none",
}

#: Rutas exentas del rate limit (scrape de métricas y WebSocket).
_EXEMPT_PREFIXES = ("/metrics", "/ws")


class SecurityMiddleware(BaseHTTPMiddleware):
    """Per-request rate limiting and defensive headers.

    Args:
        app: Wrapped ASGI app.
        settings: Security configuration.
        audit: Audit log for rate-limit breaches (optional).
    """

    def __init__(
        self,
        app: FastAPI,
        settings: SecuritySettings,
        audit: AuditLog | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._audit = audit
        self._limiter = RateLimiter(
            settings.rate_limit_requests, settings.rate_limit_window_seconds
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Enforce the rate limit, then add security headers to the response."""
        if self._should_limit(request):
            client = request.client.host if request.client else "unknown"
            if not self._limiter.allow(client):
                _log.warning("Rate limit exceeded for %s on %s", client, request.url.path)
                if self._audit is not None:
                    self._audit.record(
                        action=AuditAction.RATE_LIMIT_EXCEEDED,
                        actor=client,
                        target=request.url.path,
                    )
                return self._headers(
                    JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
                )
        response = await call_next(request)
        return self._headers(response)

    def _should_limit(self, request: Request) -> bool:
        """Whether this request is subject to the rate limiter."""
        if not (self._settings.enabled and self._settings.rate_limit_enabled):
            return False
        return not request.url.path.startswith(_EXEMPT_PREFIXES)

    def _headers(self, response: Response) -> Response:
        """Attach defensive headers when enabled."""
        if self._settings.enabled and self._settings.security_headers:
            for name, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
        return response


def add_security_middleware(
    app: FastAPI, settings: SecuritySettings, audit: AuditLog | None = None
) -> None:
    """Register :class:`SecurityMiddleware` on the app when security is enabled."""
    if not settings.enabled:
        return
    # add_middleware espera una factory; BaseHTTPMiddleware confunde a mypy con su
    # __call__, pero en runtime la construcción (app, settings, audit) es correcta.
    app.add_middleware(SecurityMiddleware, settings=settings, audit=audit)  # type: ignore[arg-type]

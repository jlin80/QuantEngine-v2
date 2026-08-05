"""Fábrica de la aplicación FastAPI del dashboard."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.routes.attribution import router as attribution_router
from app.dashboard.api.routes.audit import router as audit_router
from app.dashboard.api.routes.backtesting import router as backtesting_router
from app.dashboard.api.routes.benchmark import router as benchmark_router
from app.dashboard.api.routes.config import router as config_router
from app.dashboard.api.routes.control import router as control_router
from app.dashboard.api.routes.correlation import router as correlation_router
from app.dashboard.api.routes.costs import router as costs_router
from app.dashboard.api.routes.edge import router as edge_router
from app.dashboard.api.routes.engine import router as engine_router
from app.dashboard.api.routes.execution import router as execution_router
from app.dashboard.api.routes.forecast import router as forecast_router
from app.dashboard.api.routes.health import router as health_router
from app.dashboard.api.routes.integrations import router as integrations_router
from app.dashboard.api.routes.logs import router as logs_router
from app.dashboard.api.routes.market import router as market_router
from app.dashboard.api.routes.metrics import router as metrics_router
from app.dashboard.api.routes.microstructure import router as microstructure_router
from app.dashboard.api.routes.ml import router as ml_router
from app.dashboard.api.routes.optimizer import router as optimizer_router
from app.dashboard.api.routes.portfolio import router as portfolio_router
from app.dashboard.api.routes.production import router as production_router
from app.dashboard.api.routes.quality import router as quality_router
from app.dashboard.api.routes.rejections import router as rejections_router
from app.dashboard.api.routes.reports import router as reports_router
from app.dashboard.api.routes.research import router as research_router
from app.dashboard.api.routes.security import router as security_router
from app.dashboard.api.routes.system import router as system_router
from app.dashboard.api.websocket import router as websocket_router
from app.production.audit import AuditLog
from app.security.middleware import add_security_middleware


def create_app(settings: Settings, container: Container | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Central configuration.
        container: DI container exposing engine services (bus, health...).
            ``None`` allows running the API standalone (degraded endpoints).

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="Quant Engine API",
        version=__version__,
        description="API de observación del motor cuantitativo (salud, sistema y mercado).",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.dashboard.cors_origins,
        allow_credentials=True,
        # Fase 8 command layer needs writes; live trading stays disabled by the
        # anti-live guard, not by CORS. See ADR-058/059.
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    # Fase 9: rate limiting + cabeceras defensivas (exime /metrics y /ws).
    audit = (
        container.resolve(AuditLog)
        if container is not None and container.contains(AuditLog)
        else None
    )
    add_security_middleware(app, settings.security, audit)
    app.state.settings = settings
    app.state.container = container

    app.include_router(health_router, prefix="/api")
    app.include_router(system_router, prefix="/api")
    app.include_router(market_router, prefix="/api")
    app.include_router(engine_router, prefix="/api")
    app.include_router(edge_router, prefix="/api")
    app.include_router(attribution_router, prefix="/api")
    app.include_router(microstructure_router, prefix="/api")
    app.include_router(forecast_router, prefix="/api")
    app.include_router(correlation_router, prefix="/api")
    app.include_router(optimizer_router, prefix="/api")
    app.include_router(portfolio_router, prefix="/api")
    app.include_router(costs_router, prefix="/api")
    app.include_router(quality_router, prefix="/api")
    app.include_router(rejections_router, prefix="/api")
    app.include_router(benchmark_router, prefix="/api")
    app.include_router(execution_router, prefix="/api")
    app.include_router(ml_router, prefix="/api")
    app.include_router(backtesting_router, prefix="/api")
    # Fase 10: laboratorio de investigación (nunca opera; promoción con humano).
    app.include_router(research_router, prefix="/api")
    # Fase 8 command layer.
    app.include_router(config_router, prefix="/api")
    app.include_router(control_router, prefix="/api")
    app.include_router(integrations_router, prefix="/api")
    app.include_router(logs_router, prefix="/api")
    app.include_router(reports_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    # Fase 9: capa de producción (live gating, safe mode, kill switch).
    app.include_router(production_router, prefix="/api")
    # Fase 9: operación (backups, updates, seguridad, mantenimiento, mejoras).
    app.include_router(security_router, prefix="/api")
    # /metrics va sin prefijo: es lo que scrapea docker/prometheus/prometheus.yml.
    app.include_router(metrics_router)
    app.include_router(websocket_router)
    return app

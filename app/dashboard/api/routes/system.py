"""Endpoints de estado profundo del sistema (Health Monitor)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.monitoring.health import HealthMonitor

router = APIRouter(tags=["system"])


def _resolve_monitor(request: Request) -> HealthMonitor:
    """Fetch the HealthMonitor from the app container or fail with 503."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(HealthMonitor):
        raise HTTPException(status_code=503, detail="Health monitor not available")
    return container.resolve(HealthMonitor)


@router.get("/system/status")
async def system_status(request: Request) -> dict[str, Any]:
    """Full health snapshot (fresh sample).

    Returns:
        Serialized :class:`HealthSnapshot`.
    """
    monitor = _resolve_monitor(request)
    snapshot = await monitor.snapshot()
    return snapshot.to_dict()


@router.get("/system/info")
async def system_info(request: Request) -> dict[str, Any]:
    """Static system information (environment, instruments configured)."""
    settings = request.app.state.settings
    return {
        "app": settings.app_name,
        "environment": settings.environment.value,
        "instruments": settings.trading.instruments,
        "phase": "1 — infraestructura (sin trading)",
    }

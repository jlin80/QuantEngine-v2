"""Endpoint de liveness (barato, sin dependencias)."""

from typing import Any

from fastapi import APIRouter, Request

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe.

    Returns:
        Basic identity payload; 200 means the process is alive.
    """
    settings = request.app.state.settings
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment.value,
    }

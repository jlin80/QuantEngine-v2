"""Endpoint /metrics para Prometheus.

Se monta **sin el prefijo ``/api``** a propósito: ``docker/prometheus/
prometheus.yml`` scrapea ``backend:8000/metrics`` desde la Fase 1, y ese target
llevaba ocho fases devolviendo 404.
"""

from fastapi import APIRouter, Request, Response

from app.production.metrics import CONTENT_TYPE, render

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Return the Prometheus text exposition payload."""
    payload = render(request.app.state.container)
    return Response(content=payload, media_type=CONTENT_TYPE)

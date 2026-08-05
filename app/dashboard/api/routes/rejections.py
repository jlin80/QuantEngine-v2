"""Endpoints del Why Not Trade Engine (Bloque 14) — sólo observación."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.container import Container
from app.engine.rejections import RejectionStore

router = APIRouter(tags=["rejections"])


def _store(request: Request) -> RejectionStore:
    """RejectionStore or 503 (Quant Core deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(RejectionStore):
        raise HTTPException(status_code=503, detail="Why Not Trade Engine not enabled")
    return container.resolve(RejectionStore)


@router.get("/rejections/status")
async def rejections_status(request: Request) -> dict[str, Any]:
    """Estado del registro (cuántos, dónde, cuántos se perdieron)."""
    return _store(request).status()


@router.get("/rejections/summary")
async def rejections_summary(request: Request) -> dict[str, Any]:
    """Qué está costando operaciones, contado de dos formas.

    `blocked_by` cuenta cada vez que una puerta bloqueó; `sole_blocker` cuenta
    sólo cuando fue **la única**. La segunda es la que importa para decidir si
    relajar un filtro: una puerta que siempre bloquea acompañada de otras tres
    no está costando nada.
    """
    return _store(request).summary()


@router.get("/rejections")
async def rejections_recent(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    symbol: str | None = None,
) -> dict[str, Any]:
    """Rechazos recientes con su desglose completo."""
    found = _store(request).recent(limit=limit, symbol=symbol)
    return {"rejections": [record.to_dict() for record in found]}

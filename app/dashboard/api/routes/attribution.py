"""Endpoints del Edge Attribution Engine (Bloque 2) — sólo observación.

Backend del panel de atribución del dashboard. Ninguno opera ni cambia
configuración: exponen qué factores acompañaron a cada resultado.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.container import Container
from app.engine.attribution import EdgeAttributionEngine, FactorCapture

router = APIRouter(tags=["attribution"])


def _engine(request: Request) -> EdgeAttributionEngine:
    """EdgeAttributionEngine or 503 (atribución deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(EdgeAttributionEngine):
        raise HTTPException(status_code=503, detail="Edge Attribution Engine not enabled")
    return container.resolve(EdgeAttributionEngine)


@router.get("/attribution/status")
async def attribution_status(request: Request) -> dict[str, Any]:
    """Estado del motor (ciclos, factores medidos, store de fotos)."""
    container: Container | None = request.app.state.container
    status = _engine(request).status()
    if container is not None and container.contains(FactorCapture):
        status["capture"] = container.resolve(FactorCapture).status()
    return status


@router.get("/attribution/report")
async def attribution_report(request: Request) -> dict[str, Any]:
    """Informe agregado: asociación de cada factor con el resultado."""
    report = _engine(request).last_report()
    if report is None:
        return {"status": "pending", "detail": "aún no se ha generado ningún informe"}
    return report.to_dict()


@router.get("/attribution/trades")
async def attribution_trades(
    request: Request, limit: int = Query(default=20, ge=1, le=200)
) -> dict[str, Any]:
    """Explicación de las operaciones explicables más recientes."""
    found = _engine(request).explain_recent(limit=limit)
    return {"trades": [attribution.to_dict() for attribution in found]}


@router.get("/attribution/trades/{trade_id}")
async def attribution_trade(request: Request, trade_id: str) -> dict[str, Any]:
    """Explicación factor a factor de una operación concreta."""
    found = _engine(request).explain(trade_id)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sin atribución para '{trade_id}' (sin foto de factores o sin muestra)",
        )
    return found.to_dict()


@router.post("/attribution/cycle")
async def attribution_cycle(request: Request) -> dict[str, Any]:
    """Fuerza un ciclo de atribución y devuelve el informe resultante."""
    return (await _engine(request).run_cycle()).to_dict()

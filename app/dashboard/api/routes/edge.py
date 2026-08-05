"""Endpoints del Edge Research Engine (Bloque 1) — sólo observación.

Ninguno abre operaciones, cambia pesos ni habilita live trading: exponen la
medición de salud del edge y su histórico. Disparar un ciclo bajo demanda sí
está permitido porque un ciclo sólo lee resoluciones ya escritas y calcula.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.container import Container
from app.engine.edge_research import EdgeResearchEngine

router = APIRouter(tags=["edge"])


def _engine(request: Request) -> EdgeResearchEngine:
    """EdgeResearchEngine or 503 (motor de investigación deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(EdgeResearchEngine):
        raise HTTPException(status_code=503, detail="Edge Research Engine not enabled")
    return container.resolve(EdgeResearchEngine)


@router.get("/edge/status")
async def edge_status(request: Request) -> dict[str, Any]:
    """Estado compacto del motor (ciclos, muestras por estrategia, histórico)."""
    return _engine(request).status()


@router.get("/edge/report")
async def edge_report(request: Request) -> dict[str, Any]:
    """Último informe completo de salud del edge por estrategia."""
    report = _engine(request).last_report()
    if report is None:
        return {"status": "pending", "detail": "aún no se ha generado ningún informe"}
    return report.to_dict()


@router.get("/edge/strategies/{strategy}")
async def edge_strategy(request: Request, strategy: str) -> dict[str, Any]:
    """Salud actual de una estrategia concreta."""
    found = _engine(request).report_for(strategy)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Sin informe para '{strategy}'")
    return found.to_dict()


@router.get("/edge/strategies/{strategy}/history")
async def edge_strategy_history(
    request: Request,
    strategy: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Evolución histórica del edge de una estrategia (para graficar)."""
    engine = _engine(request)
    return {"strategy": strategy, "points": engine.history.series(strategy, limit=limit)}


@router.post("/edge/cycle")
async def edge_cycle(request: Request) -> dict[str, Any]:
    """Fuerza un ciclo de medición y devuelve el informe resultante."""
    return (await _engine(request).run_cycle()).to_dict()

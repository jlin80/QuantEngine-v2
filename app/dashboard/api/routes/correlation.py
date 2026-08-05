"""Endpoints de Correlation Intelligence (Bloque 5) — sólo observación."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.engine.correlation import CorrelationEngine

router = APIRouter(tags=["correlation"])


def _engine(request: Request) -> CorrelationEngine:
    """CorrelationEngine or 503 (correlación deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(CorrelationEngine):
        raise HTTPException(status_code=503, detail="Correlation Intelligence not enabled")
    return container.resolve(CorrelationEngine)


@router.get("/correlation/status")
async def correlation_status(request: Request) -> dict[str, Any]:
    """Estado del motor y último informe."""
    return _engine(request).status()


@router.get("/correlation/report")
async def correlation_report(request: Request) -> dict[str, Any]:
    """Pares medidos, liderazgo, correlación por sesión y pares omitidos."""
    report = _engine(request).last_report()
    if report is None:
        return {"status": "pending", "detail": "aún no se ha analizado nada"}
    return report.to_dict()


@router.get("/correlation/{symbol}")
async def correlation_symbol(request: Request, symbol: str) -> dict[str, Any]:
    """Símbolos medidos como correlacionados con el dado ahora mismo."""
    return {"symbol": symbol, "correlated": sorted(_engine(request).correlated_with(symbol))}


@router.post("/correlation/cycle")
async def correlation_cycle(request: Request) -> dict[str, Any]:
    """Fuerza un análisis y devuelve el informe."""
    return _engine(request).analyze().to_dict()

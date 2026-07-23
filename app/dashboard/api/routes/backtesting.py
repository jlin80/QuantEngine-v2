"""Endpoints de observación del laboratorio de backtesting (Fase 6).

Solo lectura: exponen el estado del laboratorio, los umbrales de calificación,
los experimentos registrados y los benchmarks disponibles. Lanzar backtests u
optimizaciones (operaciones pesadas) se hace por script/CLI, no por HTTP.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.backtesting import BacktestLab
from app.core.container import Container

router = APIRouter(tags=["backtesting"])


def _lab(request: Request) -> BacktestLab:
    """BacktestLab or 503 (laboratorio deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(BacktestLab):
        raise HTTPException(status_code=503, detail="Backtesting lab not enabled")
    return container.resolve(BacktestLab)


@router.get("/backtesting/status")
async def backtesting_status(request: Request) -> dict[str, Any]:
    """Estado del laboratorio: método de optimización, criterios y experimentos."""
    return _lab(request).status()


@router.get("/backtesting/criteria")
async def backtesting_criteria(request: Request) -> dict[str, Any]:
    """Umbrales mínimos de calificación de estrategias."""
    return _lab(request).criteria()


@router.get("/backtesting/experiments")
async def backtesting_experiments(request: Request, limit: int = 50) -> dict[str, Any]:
    """Experimentos registrados (append-only), del más reciente al más antiguo."""
    experiments = _lab(request).experiments.all()
    recent = list(reversed(experiments))[:limit]
    return {"count": len(experiments), "experiments": [exp.to_dict() for exp in recent]}

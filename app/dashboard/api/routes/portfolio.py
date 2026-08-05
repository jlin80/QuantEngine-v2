"""Endpoints de Portfolio Intelligence (Bloque 8) — sólo observación."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.portfolio import PortfolioIntelligence

router = APIRouter(tags=["portfolio"])


def _intelligence(request: Request) -> PortfolioIntelligence:
    """PortfolioIntelligence or 503 (ejecución deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(PortfolioIntelligence):
        raise HTTPException(status_code=503, detail="Portfolio Intelligence not enabled")
    return container.resolve(PortfolioIntelligence)


@router.get("/portfolio/report")
async def portfolio_report(request: Request) -> dict[str, Any]:
    """Desglose del PnL por símbolo, estrategia, sesión y régimen."""
    return _intelligence(request).analyze().to_dict()


@router.get("/portfolio/status")
async def portfolio_status(request: Request) -> dict[str, Any]:
    """Último informe calculado (sin recalcular)."""
    return _intelligence(request).status()

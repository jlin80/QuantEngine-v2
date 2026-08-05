"""Endpoints del Data Quality Engine (Bloque 11) — sólo observación."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.monitoring.data_quality import DataQualityEngine
from app.monitoring.meta_risk import MetaRiskEngine

router = APIRouter(tags=["quality"])


def _engine(request: Request) -> DataQualityEngine:
    """DataQualityEngine or 503 (Data Engine deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(DataQualityEngine):
        raise HTTPException(status_code=503, detail="Data Quality Engine not enabled")
    return container.resolve(DataQualityEngine)


@router.get("/quality/status")
async def quality_status(request: Request) -> dict[str, Any]:
    """Último informe de calidad del dato (sin re-medir)."""
    return _engine(request).status()


@router.post("/quality/measure")
async def quality_measure(request: Request) -> dict[str, Any]:
    """Fuerza una medición y devuelve el informe con su multiplicador."""
    return _engine(request).measure().to_dict()


def _meta_risk(request: Request) -> MetaRiskEngine:
    """MetaRiskEngine or 503 (Data Engine deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(MetaRiskEngine):
        raise HTTPException(status_code=503, detail="Meta Risk Engine not enabled")
    return container.resolve(MetaRiskEngine)


@router.get("/quality/meta-risk")
async def meta_risk_status(request: Request) -> dict[str, Any]:
    """Salud de la infraestructura y multiplicador compuesto (Bloque 12)."""
    return _meta_risk(request).status()


@router.post("/quality/meta-risk/measure")
async def meta_risk_measure(request: Request) -> dict[str, Any]:
    """Fuerza una medicion de infraestructura y devuelve el informe."""
    return _meta_risk(request).measure().to_dict()

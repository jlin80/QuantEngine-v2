"""Endpoints del Cost Attribution Engine (Bloque 9) — sólo observación."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.execution.costs import CostAttributionEngine

router = APIRouter(tags=["costs"])


def _engine(request: Request) -> CostAttributionEngine:
    """CostAttributionEngine or 503 (ejecución deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(CostAttributionEngine):
        raise HTTPException(status_code=503, detail="Cost Attribution Engine not enabled")
    return container.resolve(CostAttributionEngine)


@router.get("/costs/report")
async def costs_report(request: Request) -> dict[str, Any]:
    """Reparto del bruto: comisiones, slippage, spread, residuo y oportunidad."""
    return _engine(request).analyze().to_dict()


@router.get("/costs/daily")
async def costs_daily(request: Request) -> dict[str, Any]:
    """Sólo el desglose diario, para el informe recurrente.

    Las notas viajan también aquí: un informe diario que oculte "hay costes que
    no se están midiendo" invita a leer el reparto como si estuviera completo.
    """
    report = _engine(request).analyze()
    return {
        "daily": {day: breakdown.to_dict() for day, breakdown in sorted(report.daily.items())},
        "notes": list(report.notes),
    }


@router.get("/costs/status")
async def costs_status(request: Request) -> dict[str, Any]:
    """Último informe calculado (sin recalcular)."""
    return _engine(request).status()

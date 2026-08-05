"""Endpoints del Regime Forecast Engine (Bloque 4) — sólo observación.

El endpoint de validación es el importante: un pronóstico sin su historial de
aciertos es una opinión con decimales.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.engine.regime_forecast import RegimeForecastService

router = APIRouter(tags=["forecast"])


def _service(request: Request) -> RegimeForecastService:
    """RegimeForecastService or 503 (pronóstico deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(RegimeForecastService):
        raise HTTPException(status_code=503, detail="Regime Forecast Engine not enabled")
    return container.resolve(RegimeForecastService)


@router.get("/forecast/status")
async def forecast_status(request: Request) -> dict[str, Any]:
    """Estado del pronóstico: condiciones aprendidas, pendientes y marcador."""
    return _service(request).status()


@router.get("/forecast/{symbol}")
async def forecast_symbol(request: Request, symbol: str) -> dict[str, Any]:
    """Pronóstico vigente de un símbolo (o por qué no lo hay)."""
    found = _service(request).last(symbol)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Sin pronóstico para '{symbol}'")
    return found.to_dict()


@router.post("/forecast/cycle")
async def forecast_cycle(request: Request) -> dict[str, Any]:
    """Fuerza un ciclo: resuelve los pendientes y emite pronósticos nuevos."""
    forecasts = await _service(request).run_cycle()
    return {symbol: forecast.to_dict() for symbol, forecast in forecasts.items()}

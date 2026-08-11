"""Explicación por operación — sólo lectura.

`GET /api/trades/{trade_id}/explain` responde, para una operación cerrada, qué
la disparó, qué confirmaciones faltaron, por qué salió donde salió y cuánto se
dejó la ejecución respecto a lo que daba la señal. No opera, no cambia
configuración y no habilita nada.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.engine.trade_explain import TradeExplainer

router = APIRouter(tags=["explain"])


def _explainer(request: Request) -> TradeExplainer:
    """TradeExplainer or 503 (motor sin journal o sin historial de señales)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(TradeExplainer):
        raise HTTPException(status_code=503, detail="Trade explainer not available")
    return container.resolve(TradeExplainer)


@router.get("/trades/{trade_id}/explain")
async def explain_trade(request: Request, trade_id: str) -> dict[str, Any]:
    """Explicación completa de una operación cerrada."""
    found = _explainer(request).explain(trade_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Operación desconocida: '{trade_id}'")
    return found.to_dict()

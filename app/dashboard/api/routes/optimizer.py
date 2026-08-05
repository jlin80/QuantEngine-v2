"""Endpoints del Execution Optimizer (Bloque 6) — cotizar, no ejecutar.

Estos endpoints **no mandan ninguna orden**: cotizan las tres tácticas para un
escenario dado y devuelven la elegida con sus alternativas. Sirven para auditar
por qué el motor elige lo que elige, no para operar desde el dashboard.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.container import Container
from app.execution.optimizer import ExecutionContext, ExecutionOptimizer

router = APIRouter(tags=["optimizer"])


def _optimizer(request: Request) -> ExecutionOptimizer:
    """ExecutionOptimizer or 503 (ejecución deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(ExecutionOptimizer):
        raise HTTPException(status_code=503, detail="Execution Optimizer not enabled")
    return container.resolve(ExecutionOptimizer)


@router.get("/optimizer/plan")
async def optimizer_plan(
    request: Request,
    symbol: str,
    quantity: float = Query(default=0.0, ge=0.0),
    spread_bps: float | None = None,
    atr_pct: float | None = None,
    session: str = "off",
    available_liquidity: float = Query(default=0.0, ge=0.0),
    queue_imbalance: float | None = Query(default=None, ge=-1.0, le=1.0),
    urgency: float = Query(default=0.5, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Cotiza IOC/LIMIT/MARKET para un escenario y devuelve la mejor."""
    context = ExecutionContext(
        symbol=symbol,
        quantity=quantity,
        spread_bps=spread_bps,
        atr_pct=atr_pct,
        session=session,
        available_liquidity=available_liquidity,
        queue_imbalance=queue_imbalance,
        urgency=urgency,
    )
    return _optimizer(request).optimize(context).to_dict()

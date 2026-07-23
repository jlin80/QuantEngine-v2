"""Endpoints de observación del Quant Core (Fase 3)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.engine.models import SignalStatus
from app.engine.quant_core import QuantCore

router = APIRouter(tags=["engine"])


def _core(request: Request) -> QuantCore:
    """QuantCore or 503 (Quant Core deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(QuantCore):
        raise HTTPException(status_code=503, detail="Quant Core not enabled")
    return container.resolve(QuantCore)


@router.get("/engine/status")
async def engine_status(request: Request) -> dict[str, Any]:
    """Estado completo del núcleo (estrategias, señales, consenso, filtros)."""
    return _core(request).status()


@router.get("/engine/strategies")
async def engine_strategies(request: Request) -> dict[str, Any]:
    """Estrategias cargadas con sus métricas de ejecución."""
    core = _core(request)
    return {
        "strategies": [stats.to_dict() for stats in core.strategies.stats()],
        "explanations": {name: core.strategies.explain(name) for name in core.strategies.loaded},
    }


@router.get("/engine/signals")
async def engine_signals(
    request: Request, limit: int = 50, status: str | None = None
) -> dict[str, Any]:
    """Historial de señales (aceptadas, rechazadas, expiradas...)."""
    core = _core(request)
    parsed: SignalStatus | None = None
    if status is not None:
        try:
            parsed = SignalStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Unknown status '{status}'") from exc
    records = core.history.signals(limit=limit, status=parsed)
    return {"signals": [record.to_dict() for record in records]}


@router.get("/engine/decisions")
async def engine_decisions(
    request: Request, limit: int = 50, symbol: str | None = None
) -> dict[str, Any]:
    """Decisiones recientes con su explicación completa."""
    decisions = _core(request).history.decisions(limit=limit, symbol=symbol)
    return {"decisions": [decision.to_dict() for decision in decisions]}


@router.get("/engine/regime/{symbol}")
async def engine_regime(symbol: str, request: Request) -> dict[str, Any]:
    """Régimen de mercado detectado para un símbolo."""
    return _core(request).detect_regime(symbol).to_dict()


@router.get("/engine/context/{symbol}")
async def engine_context(symbol: str, request: Request) -> dict[str, Any]:
    """Contexto de mercado completo para un símbolo."""
    context = await _core(request).analyze_market(symbol)
    return context.to_dict()


@router.get("/engine/filters/{symbol}")
async def engine_filters(symbol: str, request: Request) -> dict[str, Any]:
    """Evaluación actual de todos los filtros para un símbolo."""
    results = await _core(request).evaluate_filters(symbol)
    return {"symbol": symbol.upper(), "filters": [r.to_dict() for r in results]}


@router.get("/engine/consensus")
async def engine_consensus(request: Request) -> dict[str, Any]:
    """Configuración y estado del motor de consenso."""
    return _core(request).consensus.status()


@router.get("/engine/performance")
async def engine_performance(request: Request) -> dict[str, Any]:
    """Evaluación continua: métricas virtuales por estrategia (Fase 4)."""
    core = _core(request)
    if core.performance is None:
        raise HTTPException(status_code=503, detail="Performance tracker not enabled")
    return core.performance.status()


@router.get("/engine/strategies/{name}")
async def engine_strategy_detail(name: str, request: Request) -> dict[str, Any]:
    """Detalle de una estrategia: métricas, explicación y rendimiento."""
    core = _core(request)
    for stats in core.strategies.stats():
        if stats.name == name:
            performance = (
                core.performance.performance(name) if core.performance is not None else None
            )
            return {
                "stats": stats.to_dict(),
                "explanation": core.strategies.explain(name),
                "performance": performance.to_dict() if performance else None,
            }
    raise HTTPException(status_code=404, detail=f"Strategy '{name}' not loaded")

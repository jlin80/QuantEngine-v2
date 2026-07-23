"""Endpoints de observación del Execution Engine (Fase 5, paper trading)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.execution.api import ExecutionCore

router = APIRouter(tags=["execution"])


def _core(request: Request) -> ExecutionCore:
    """ExecutionCore or 503 (Execution Engine deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(ExecutionCore):
        raise HTTPException(status_code=503, detail="Execution Engine not enabled")
    return container.resolve(ExecutionCore)


@router.get("/execution/status")
async def execution_status(request: Request) -> dict[str, Any]:
    """Estado completo del motor de ejecución (paper)."""
    return _core(request).status()


@router.get("/execution/portfolio")
async def execution_portfolio(request: Request) -> dict[str, Any]:
    """Instantánea del portafolio (balance, equity, drawdown, exposición)."""
    return _core(request).portfolio_snapshot()


@router.get("/execution/positions")
async def execution_positions(request: Request) -> dict[str, Any]:
    """Posiciones abiertas y cerradas."""
    core = _core(request)
    return {"open": core.open_positions(), "closed": core.closed_positions()}


@router.get("/execution/trades")
async def execution_trades(request: Request, limit: int = 100) -> dict[str, Any]:
    """Historial de operaciones del Trade Journal."""
    return {"trades": _core(request).trades(limit)}


@router.get("/execution/performance")
async def execution_performance(request: Request) -> dict[str, Any]:
    """Métricas de desempeño (win rate, PF, Sharpe, drawdown...)."""
    return _core(request).performance_report()


@router.get("/execution/risk")
async def execution_risk(request: Request) -> dict[str, Any]:
    """Estado del Risk Manager (límites, kill switch, circuit breaker)."""
    return _core(request).risk_status()


@router.get("/execution/report")
async def execution_report(request: Request) -> dict[str, Any]:
    """Reporte combinado de cartera, rendimiento y riesgo."""
    return _core(request).generate_trade_report()


@router.get("/execution/notifications")
async def execution_notifications(request: Request, limit: int = 20) -> dict[str, Any]:
    """Últimas notificaciones de ejecución enviadas a Discord."""
    return {"notifications": _core(request).recent_notifications(limit)}

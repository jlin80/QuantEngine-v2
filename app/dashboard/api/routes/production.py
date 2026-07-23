"""Endpoints de la capa de producción (Fase 9).

Toda escritura sigue el orden invariante del proyecto: guard → mutar → auditar
→ devolver. El guard anti-live de la Fase 8 se mantiene, pero ya no es la única
barrera: habilitar live exige además superar el Live Gate completo, que estos
endpoints no pueden saltarse — sólo consultarlo.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.production.api import ProductionAPI
from app.production.kill_switch import KillSwitchTrigger

router = APIRouter(tags=["production"])


def _core(request: Request) -> ProductionAPI:
    """Resolve the production facade or answer 503.

    Args:
        request: Incoming request.

    Returns:
        The production facade.

    Raises:
        HTTPException: 503 if the production layer is not enabled.
    """
    container = request.app.state.container
    if container is None or not container.contains(ProductionAPI):
        raise HTTPException(status_code=503, detail="Production layer not enabled")
    resolved: ProductionAPI = container.resolve(ProductionAPI)
    return resolved


def _actor(payload: dict[str, Any], request: Request) -> str:
    """Resolve the acting operator, falling back to the configured one."""
    actor = str(payload.get("actor") or "").strip()
    if actor:
        return actor
    configured = str(request.app.state.settings.production.operator or "").strip()
    return configured or "dashboard"


@router.get("/production/status")
async def production_status(request: Request) -> dict[str, Any]:
    """Return the compact production status (mode, safe mode, kill switch)."""
    return _core(request).system_status()


@router.get("/production/health")
async def production_health(request: Request) -> dict[str, Any]:
    """Return the aggregated production health report."""
    return await _core(request).health_report()


@router.get("/production/live/report")
async def live_gate_report(request: Request) -> dict[str, Any]:
    """Evaluate and return the Live Gate report, criterion by criterion."""
    return _core(request).evaluate_live_gate()


@router.post("/production/live/approve")
async def approve_live(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Record the operator's explicit approval of the current gate report.

    Approving satisfies one criterion — it does not enable live trading.
    """
    core = _core(request)
    try:
        return core.approve_live_trading(
            actor=_actor(payload, request), reason=str(payload.get("reason", ""))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/production/live/enable")
async def enable_live(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Attempt to enable live trading (rejected unless every criterion passes)."""
    return await _core(request).enable_live_trading(actor=_actor(payload, request))


@router.post("/production/live/disable")
async def disable_live(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Disable live trading by revoking the operator approval."""
    return await _core(request).disable_live_trading(
        actor=_actor(payload, request), reason=str(payload.get("reason", ""))
    )


@router.post("/production/safe_mode")
async def activate_safe_mode(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Force Safe Mode on (manual operator action)."""
    return await _core(request).activate_safe_mode(
        reason=str(payload.get("reason", "activación manual"))
    )


@router.post("/production/kill_switch")
async def engage_kill_switch(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Engage the global kill switch."""
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=422, detail="El kill switch exige un motivo")
    return await _core(request).kill_switch_engage(
        actor=_actor(payload, request), reason=reason, trigger=KillSwitchTrigger.MANUAL
    )


@router.post("/production/kill_switch/release")
async def release_kill_switch(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Release the global kill switch (requires actor and reason)."""
    try:
        return await _core(request).kill_switch_release(
            actor=_actor(payload, request), reason=str(payload.get("reason", ""))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

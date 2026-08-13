"""Endpoints to read and update whitelisted runtime configuration."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.dashboard.api.audit import audit_log
from app.dashboard.api.config_store import _is_live, config_store
from app.dashboard.api.guard import assert_no_live_switch
from app.production.api import ProductionAPI
from app.production.kill_switch import KillSwitchTrigger

router = APIRouter(tags=["config"])

IGNORE_DRAWDOWN = "execution.risk.ignore_drawdown_limits"
"""Toggle que desactiva todas las paradas por drawdown."""


async def _release_drawdown_kill_switch(request: Request) -> None:
    """Suelta el kill switch global si había saltado por drawdown.

    El Risk Manager libera su propio switch en la siguiente vuelta, pero el
    ``KillSwitchController`` persiste el suyo en disco: sin esto, activar el
    toggle dejaría el bot operando hasta el próximo reinicio, y ahí el switch
    persistido volvería a saltar. Un switch disparado por otra causa (manual,
    programado, riesgo) no se toca.
    """
    container = request.app.state.container
    if container is None or not container.contains(ProductionAPI):
        return
    production: ProductionAPI = container.resolve(ProductionAPI)
    status = production.kill_switch.status()
    if not status.get("active") or str(status.get("trigger")) != str(KillSwitchTrigger.DRAWDOWN):
        return
    actor = str(request.app.state.settings.production.operator or "").strip() or "dashboard"
    await production.kill_switch_release(
        actor=actor,
        reason="ignore_drawdown_limits activado desde el dashboard",
    )


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Return effective whitelisted config and the resolved (paper) mode."""
    settings = request.app.state.settings
    return {
        "mode": str(settings.execution.resolved_mode()),
        "live_enabled": False,
        "config": config_store.effective(settings),
    }


@router.patch("/config")
async def patch_config(request: Request, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a whitelisted config patch (guarded against live, audited)."""
    assert_no_live_switch(patch)
    settings = request.app.state.settings
    try:
        applied = config_store.apply(settings, patch)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Key not allowed: {exc}") from exc
    except ValueError as exc:
        # El valor no encaja con el tipo declarado. Devolver el motivo importa:
        # un 500 genérico aquí obligaba a leer el log del servidor para saber
        # que faltaba una coma en un JSON.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit_log.record(action="config.patch", after=applied)
    if applied.get(IGNORE_DRAWDOWN):
        await _release_drawdown_kill_switch(request)
    hot = sorted(k for k in applied if _is_live(k))
    needs_restart = sorted(k for k in applied if not _is_live(k))
    return {
        "applied": applied,
        "applied_live": hot,
        "needs_restart": needs_restart,
        "config": config_store.effective(settings),
    }

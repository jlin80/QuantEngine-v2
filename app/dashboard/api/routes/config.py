"""Endpoints to read and update whitelisted runtime configuration."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.dashboard.api.audit import audit_log
from app.dashboard.api.config_store import config_store
from app.dashboard.api.guard import assert_no_live_switch

router = APIRouter(tags=["config"])


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
    audit_log.record(action="config.patch", after=applied)
    return {"applied": applied, "config": config_store.effective(settings)}

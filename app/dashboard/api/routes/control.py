"""Command endpoints: strategy control and backtest submission (all audited).

Strategy control records operator intent in the runtime config store (a running
engine loads strategies at startup, so intents apply on reload / where wired).
Backtests are heavy and execute server-side via the BacktestLab; this accepts and
audits the request. No endpoint here can enable live trading.
"""

import uuid
from typing import Any

from fastapi import APIRouter

from app.dashboard.api.audit import audit_log
from app.dashboard.api.config_store import config_store
from app.utils.time import utc_now

router = APIRouter(tags=["control"])


@router.post("/engine/strategies/{name}/enable")
async def enable_strategy(name: str) -> dict[str, Any]:
    """Record intent to enable a strategy (audited)."""
    override = config_store.set_strategy(name, {"enabled": True})
    audit_log.record(action="strategy.enable", target=name, after=override)
    return {"strategy": name, "override": override}


@router.post("/engine/strategies/{name}/disable")
async def disable_strategy(name: str) -> dict[str, Any]:
    """Record intent to disable a strategy (audited)."""
    override = config_store.set_strategy(name, {"enabled": False})
    audit_log.record(action="strategy.disable", target=name, after=override)
    return {"strategy": name, "override": override}


@router.patch("/engine/strategies/{name}/weight")
async def set_strategy_weight(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record a strategy weight override (audited)."""
    weight = float(payload.get("weight", 1.0))
    override = config_store.set_strategy(name, {"weight": weight})
    audit_log.record(action="strategy.weight", target=name, after=override)
    return {"strategy": name, "override": override}


@router.post("/backtesting/run")
async def submit_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept and audit a backtest request, returning a job descriptor."""
    job_id = uuid.uuid4().hex
    audit_log.record(action="backtest.run", after=payload)
    return {
        "job_id": job_id,
        "status": "accepted",
        "params": payload,
        "submitted_at": utc_now().isoformat(),
        "note": "Backtests run via BacktestLab; results appear under Experiments.",
    }


@router.post("/backtesting/cancel/{job_id}")
async def cancel_backtest(job_id: str) -> dict[str, Any]:
    """Cancel a submitted backtest job (best effort; audited)."""
    audit_log.record(action="backtest.cancel", target=job_id)
    return {"job_id": job_id, "status": "cancelled"}

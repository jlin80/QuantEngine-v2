"""Endpoint exposing the recent-errors buffer for the logs viewer."""

from typing import Any

from fastapi import APIRouter

from app.logging.recent import get_recent_errors

router = APIRouter(tags=["logs"])


@router.get("/logs")
async def get_logs(limit: int = 200, level: str | None = None) -> dict[str, Any]:
    """Return recent buffered log records with an optional level filter.

    The engine buffers recent error-level records for the Health Monitor; this
    exposes them for the dashboard log viewer (streaming is layered on top via
    the event WebSocket).
    """
    records: list[dict[str, Any]] = [
        {"timestamp": e.timestamp, "level": e.level, "logger": e.logger, "message": e.message}
        for e in get_recent_errors()
    ]
    if level is not None:
        wanted = level.lower()
        records = [r for r in records if str(r["level"]).lower() == wanted]
    return {"logs": records[-limit:], "count": len(records)}

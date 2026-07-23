"""Endpoint exposing the audit trail.

Desde la Fase 9 el motor tiene su propio ``AuditLog`` en el contenedor (el que
registra arranques, kill switch, safe mode y live gating). Se prefiere ése: si
el endpoint leyera siempre el singleton del módulo, el dashboard mostraría un
registro distinto del que el motor está escribiendo de verdad.
"""

from typing import Any

from fastapi import APIRouter, Request

from app.dashboard.api.audit import audit_log
from app.production.audit import AuditLog

router = APIRouter(tags=["audit"])


def _log(request: Request) -> AuditLog:
    """Return the engine's audit log, or the process-wide singleton."""
    container = request.app.state.container
    if container is not None and container.contains(AuditLog):
        resolved: AuditLog = container.resolve(AuditLog)
        return resolved
    return audit_log


@router.get("/audit")
async def get_audit(
    request: Request, limit: int = 100, action: str | None = None
) -> dict[str, Any]:
    """Return recent audited actions (newest first), optionally filtered."""
    return {"entries": _log(request).recent(limit, action=action)}

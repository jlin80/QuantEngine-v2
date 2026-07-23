"""Endpoints de operación de la Fase 9.

Backups, actualizaciones, seguridad, mantenimiento, mejora continua, licencias,
sincronización de Notion y reportes bajo demanda. Todo se apoya en la fachada
:class:`ProductionAPI`; ninguna acción puede habilitar live (eso sólo lo hace el
Live Gate en ``routes/production.py``). Las escrituras siguen el orden del
proyecto: guard → mutar → auditar → devolver.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.production.api import ProductionAPI

router = APIRouter(tags=["operations"])


def _core(request: Request) -> ProductionAPI:
    """Resolve the production facade or answer 503."""
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


# ----------------------------------------------------------------------
# Seguridad
# ----------------------------------------------------------------------


@router.get("/security/report")
async def security_report(request: Request) -> dict[str, Any]:
    """Configuration validation + secret rotation status."""
    return _core(request).security_report()


# ----------------------------------------------------------------------
# Backups
# ----------------------------------------------------------------------


@router.get("/backups")
async def list_backups(request: Request) -> dict[str, Any]:
    """List available backups (newest first)."""
    return {"backups": _core(request).list_backups()}


@router.post("/backups/create")
async def create_backup(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Create a manual backup of the critical state."""
    return await _core(request).backup_database(kind=str(payload.get("kind", "manual")))


@router.post("/backups/restore")
async def restore_backup(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Restore a backup by id (verifies integrity first)."""
    backup_id = str(payload.get("backup_id", "")).strip()
    if not backup_id:
        raise HTTPException(status_code=422, detail="backup_id requerido")
    try:
        return await _core(request).restore_database(
            backup_id=backup_id, actor=_actor(payload, request)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# Actualizaciones
# ----------------------------------------------------------------------


@router.get("/updates/check")
async def check_updates(request: Request) -> dict[str, Any]:
    """Check for available updates against the configured manifest."""
    return _core(request).check_updates()


# ----------------------------------------------------------------------
# Mantenimiento
# ----------------------------------------------------------------------


@router.post("/maintenance/enter")
async def enter_maintenance(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Open a maintenance window."""
    core = _core(request)
    if core.maintenance is None:
        raise HTTPException(status_code=503, detail="Maintenance manager not enabled")
    return core.maintenance.enter(
        actor=_actor(payload, request), reason=str(payload.get("reason", ""))
    )


@router.post("/maintenance/exit")
async def exit_maintenance(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Close the maintenance window."""
    core = _core(request)
    if core.maintenance is None:
        raise HTTPException(status_code=503, detail="Maintenance manager not enabled")
    return core.maintenance.exit(actor=_actor(payload, request))


# ----------------------------------------------------------------------
# Mejora continua, Notion y reportes
# ----------------------------------------------------------------------


@router.post("/improvement/analyze")
async def run_improvement(request: Request) -> dict[str, Any]:
    """Run the continuous-improvement analysis and register findings."""
    return await _core(request).run_improvement_analysis()


@router.post("/notion/sync")
async def sync_notion(request: Request) -> dict[str, Any]:
    """Flush the Notion documentation queue."""
    return await _core(request).sync_notion()


@router.post("/reports/send")
async def send_report(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Send an operational report on demand (``period``: hourly|daily)."""
    period = str(payload.get("period", "daily")).strip().lower()
    core = _core(request)
    if period == "hourly":
        return await core.send_hourly_report()
    return await core.send_daily_report()

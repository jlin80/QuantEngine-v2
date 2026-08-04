"""Endpoints de estado profundo del sistema (Health Monitor) y reinicio."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.dashboard.api.audit import audit_log
from app.execution.api import ExecutionCore
from app.monitoring.health import HealthMonitor

router = APIRouter(tags=["system"])


def _resolve_monitor(request: Request) -> HealthMonitor:
    """Fetch the HealthMonitor from the app container or fail with 503."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(HealthMonitor):
        raise HTTPException(status_code=503, detail="Health monitor not available")
    return container.resolve(HealthMonitor)


@router.get("/system/status")
async def system_status(request: Request) -> dict[str, Any]:
    """Full health snapshot (fresh sample).

    Returns:
        Serialized :class:`HealthSnapshot`.
    """
    monitor = _resolve_monitor(request)
    snapshot = await monitor.snapshot()
    return snapshot.to_dict()


@router.get("/system/info")
async def system_info(request: Request) -> dict[str, Any]:
    """Static system information (environment, instruments configured)."""
    settings = request.app.state.settings
    return {
        "app": settings.app_name,
        "environment": settings.environment.value,
        "instruments": settings.trading.instruments,
        "phase": "1 — infraestructura (sin trading)",
    }


@router.post("/system/restart")
async def system_restart(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Request an orderly engine restart (guarded, audited, confirmed).

    No toca el planificador de tareas de Windows ni necesita permisos sobre
    servicios: el motor simplemente se detiene de forma ordenada y el watchdog
    (``run_v2.ps1``) lo relanza en <=10 s. El apagado cierra los servicios en
    orden inverso, así que el journal se vacía a disco y nada queda a medias.

    Guardas, en orden:
      1. ``confirm`` explícito en el cuerpo — evita reinicios por un clic suelto
         o por una petición cruzada.
      2. **Ninguna posición abierta**. Reiniciar con posiciones vivas las deja
         sin gestión (ni trailing, ni break-even, ni salida por régimen) durante
         el arranque; el broker sólo respetaría el SL/TP ya puesto.

    Args:
        request: Petición HTTP (de donde sale el contenedor de la app).
        body: ``{"confirm": true}``.

    Returns:
        Confirmación con el número de segundos que tarda el watchdog.

    Raises:
        HTTPException: 400 sin confirmación, 409 con posiciones abiertas,
            503 si el motor no está disponible.
    """
    if body.get("confirm") is not True:
        raise HTTPException(
            status_code=400, detail="Falta la confirmación explícita ({'confirm': true})"
        )

    # Import diferido a propósito: `app.engine.engine` importa la app del
    # dashboard, que importa este módulo. Al nivel superior el ciclo es real y
    # rompe cualquier proceso que importe `app.engine.engine` primero (lo hacía
    # fallar según el orden de importación, no según el código).
    from app.engine.engine import QuantEngine

    container: Container | None = request.app.state.container
    if container is None or not container.contains(QuantEngine):
        raise HTTPException(status_code=503, detail="Engine not available for restart")

    open_positions: list[Any] = []
    if container.contains(ExecutionCore):
        open_positions = container.resolve(ExecutionCore).open_positions()
    if open_positions:
        symbols = sorted({str(p.get("symbol", "?")) for p in open_positions})
        raise HTTPException(
            status_code=409,
            detail=(
                f"Hay {len(open_positions)} posición(es) abierta(s) en {', '.join(symbols)}. "
                "Ciérralas antes de reiniciar para no dejarlas sin gestión."
            ),
        )

    audit_log.record(
        action="system.restart",
        actor="dashboard",
        meta={"reason": str(body.get("reason", ""))[:200]},
    )
    engine = container.resolve(QuantEngine)
    # Se detiene DESPUÉS de responder: si no, el cliente nunca recibiría el 200.
    asyncio.get_running_loop().call_later(0.5, engine.request_stop)
    return {
        "restarting": True,
        "detail": "El motor se está deteniendo; el watchdog lo relanzará en unos segundos.",
        "expected_downtime_seconds": 15,
    }

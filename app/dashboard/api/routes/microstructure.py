"""Endpoints del Microstructure Engine (Bloque 3) — sólo observación.

Con un proveedor que no publica libro (MT5, el bróker de la demo) estos
endpoints responden `observable: false` con su motivo, en vez de devolver ceros
que parecerían un libro medido y equilibrado.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.engine.microstructure import MicrostructureEngine

router = APIRouter(tags=["microstructure"])


def _engine(request: Request) -> MicrostructureEngine:
    """MicrostructureEngine or 503 (Data Engine deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(MicrostructureEngine):
        raise HTTPException(status_code=503, detail="Microstructure Engine not enabled")
    return container.resolve(MicrostructureEngine)


@router.get("/microstructure/status")
async def microstructure_status(request: Request) -> dict[str, Any]:
    """Estado por símbolo: eventos observados y último snapshot."""
    return _engine(request).status()


@router.get("/microstructure/{symbol}")
async def microstructure_symbol(request: Request, symbol: str) -> dict[str, Any]:
    """Microestructura actual de un símbolo (o por qué no es observable)."""
    return _engine(request).snapshot(symbol).to_dict()

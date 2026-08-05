"""Endpoints del Live Shadow Benchmark (Bloque 15) — sólo observación.

Ninguno habilita live trading ni lo prepara: el carril live se reporta como
ausente, con su motivo, y el informe lleva `live_enabled: false` explícito.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.execution.benchmark import ShadowBenchmark

router = APIRouter(tags=["benchmark"])


def _benchmark(request: Request) -> ShadowBenchmark:
    """ShadowBenchmark or 503 (ejecución deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(ShadowBenchmark):
        raise HTTPException(status_code=503, detail="Live Shadow Benchmark not enabled")
    return container.resolve(ShadowBenchmark)


@router.get("/benchmark/report")
async def benchmark_report(request: Request) -> dict[str, Any]:
    """Compara paper contra el fill ideal, con el carril live declarado."""
    return _benchmark(request).analyze().to_dict()


@router.get("/benchmark/status")
async def benchmark_status(request: Request) -> dict[str, Any]:
    """Último informe (sin recalcular) y el invariante de live deshabilitado."""
    return _benchmark(request).status()

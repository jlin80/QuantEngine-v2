"""Endpoints del Quant Research Lab (Fase 10) — observación y ciclo de vida.

El laboratorio **no opera**: estos endpoints exponen el estado, las candidatas,
los experimentos, la base de conocimiento y el ranking, y permiten generar
estrategias, crear/archivar experimentos y promover/rechazar candidatas. Ninguno
abre operaciones ni habilita live trading; la promoción exige aprobación humana.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.core.exceptions import ResearchError
from app.research.api import ResearchLab
from app.research.models import Hypothesis

router = APIRouter(tags=["research"])


def _lab(request: Request) -> ResearchLab:
    """ResearchLab or 503 (laboratorio de investigación deshabilitado)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(ResearchLab):
        raise HTTPException(status_code=503, detail="Research Lab not enabled")
    return container.resolve(ResearchLab)


# ---------------------------------------------------------------------------
# Observación (paneles del dashboard)
# ---------------------------------------------------------------------------


@router.get("/research/status")
async def research_status(request: Request) -> dict[str, Any]:
    """Estado compacto del laboratorio (experimentos, candidatas, conocimiento)."""
    return _lab(request).status()


@router.get("/research/report")
async def research_report(request: Request) -> dict[str, Any]:
    """Informe completo: candidatas + experimentos + conocimiento + bayesiano."""
    return _lab(request).report()


@router.get("/research/catalog")
async def research_catalog(request: Request) -> dict[str, Any]:
    """Catálogo de bloques de señal, filtros, features y factores."""
    return _lab(request).catalog()


@router.get("/research/candidates")
async def research_candidates(request: Request) -> dict[str, Any]:
    """Candidatas registradas (genoma, informe, paper, promoción)."""
    lab = _lab(request)
    return {"candidates": [c.to_dict() for c in lab.candidates.list()]}


@router.get("/research/experiments")
async def research_experiments(request: Request) -> dict[str, Any]:
    """Experimentos registrados (append-only)."""
    lab = _lab(request)
    return {"experiments": [e.to_dict() for e in lab.experiments.list()]}


@router.get("/research/knowledge")
async def research_knowledge(request: Request) -> dict[str, Any]:
    """Resumen de la base de conocimiento (qué funcionó / falló y por qué)."""
    return _lab(request).knowledge.summary()


@router.get("/research/ranking")
async def research_ranking(request: Request) -> dict[str, Any]:
    """Ranking de las candidatas registradas por score compuesto."""
    return {"ranking": [entry.to_dict() for entry in _lab(request).rank_strategies()]}


# ---------------------------------------------------------------------------
# Ciclo de vida (nunca opera; la promoción exige aprobación humana)
# ---------------------------------------------------------------------------


@router.post("/research/generate")
async def research_generate(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Generar un lote de estrategias experimentales (sin validar)."""
    lab = _lab(request)
    symbol = str(body.get("symbol", "BTCUSDT"))
    timeframe = str(body.get("timeframe", "1m"))
    count = body.get("count")
    genomes = lab.generate_strategy(symbol, timeframe, count=count)
    return {"generated": len(genomes), "genomes": [g.to_dict() for g in genomes]}


@router.post("/research/experiments")
async def research_create_experiment(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Crear un experimento (registra hipótesis y notifica)."""
    lab = _lab(request)
    hypothesis = None
    if body.get("hypothesis"):
        hypothesis = Hypothesis(
            text=str(body["hypothesis"]),
            rationale=str(body.get("rationale", "")),
            expected_edge=str(body.get("expected_edge", "")),
        )
    record = await lab.create_experiment(
        str(body.get("label", "experimento")),
        str(body.get("kind", "note")),
        hypothesis=hypothesis,
        payload=body.get("payload"),
    )
    return record.to_dict()


@router.post("/research/experiments/{experiment_id}/archive")
async def research_archive_experiment(
    request: Request, experiment_id: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Archivar un experimento (el conocimiento se conserva)."""
    lab = _lab(request)
    conclusions = str((body or {}).get("conclusions", ""))
    try:
        record = await lab.archive_experiment(experiment_id, conclusions)
    except ResearchError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return record.to_dict()


@router.post("/research/promote")
async def research_promote(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Evaluar la promoción de una candidata (fail-closed; nunca habilita live)."""
    lab = _lab(request)
    genome_id = body.get("genome_id")
    if not genome_id:
        raise HTTPException(status_code=400, detail="genome_id requerido")
    try:
        decision = await lab.promote_strategy(
            str(genome_id),
            operator=str(body.get("operator", "dashboard")),
            operator_approved=bool(body.get("operator_approved", False)),
            current_metrics=body.get("current_metrics"),
            drift=float(body.get("drift", 0.0)),
        )
    except ResearchError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return decision.to_dict()


@router.post("/research/reject")
async def research_reject(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Rechazar explícitamente una candidata."""
    lab = _lab(request)
    genome_id = body.get("genome_id")
    if not genome_id:
        raise HTTPException(status_code=400, detail="genome_id requerido")
    try:
        candidate = await lab.reject_strategy(str(genome_id), str(body.get("reason", "descartada")))
    except ResearchError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return candidate.to_dict()

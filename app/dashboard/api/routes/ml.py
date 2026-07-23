"""Endpoints del Machine Learning (Fase 7) — observación y acciones asesoras.

El ML **asesora, no decide**: estos endpoints exponen el estado, los modelos, el
ranking, la deriva y las features para el dashboard, y permiten disparar
predicciones/entrenamientos bajo demanda. Ninguno abre operaciones ni habilita
live trading; toda recomendación pasa por el Decision Engine y el Risk Manager.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.ml.api import MLEngine

router = APIRouter(tags=["ml"])


def _engine(request: Request) -> MLEngine:
    """MLEngine or 503 (capa de Machine Learning deshabilitada)."""
    container: Container | None = request.app.state.container
    if container is None or not container.contains(MLEngine):
        raise HTTPException(status_code=503, detail="Machine Learning not enabled")
    return container.resolve(MLEngine)


# ---------------------------------------------------------------------------
# Observación (paneles del dashboard)
# ---------------------------------------------------------------------------


@router.get("/ml/status")
async def ml_status(request: Request) -> dict[str, Any]:
    """Estado compacto del ML (registro, inferencia, deriva, meta, features)."""
    return _engine(request).status()


@router.get("/ml/report")
async def ml_report(request: Request) -> dict[str, Any]:
    """Informe completo: fichas de modelos + ranking + deriva + meta."""
    return _engine(request).report()


@router.get("/ml/models")
async def ml_models(request: Request) -> dict[str, Any]:
    """Registro de modelos (versionado, estado, activo) e historial de auditoría."""
    registry = _engine(request).registry
    active = registry.active_record()
    return {
        "models": [record.to_dict() for record in registry.records()],
        "active": active.to_dict() if active else None,
        "history": registry.history(),
    }


@router.get("/ml/ranking")
async def ml_ranking(request: Request) -> dict[str, Any]:
    """Ranking de estrategias por evidencia (reciente/histórico/segmentado)."""
    return {"ranking": [score.to_dict() for score in _engine(request).rank_strategies()]}


@router.get("/ml/features")
async def ml_features(request: Request) -> dict[str, Any]:
    """Catálogo profesional del Feature Store (nombre, versión, fuente, tipo)."""
    return {"features": _engine(request).feature_catalog()}


@router.get("/ml/meta")
async def ml_meta(request: Request) -> dict[str, Any]:
    """Estado del Meta Strategy Manager (pesos dinámicos, activas, desactivadas)."""
    return _engine(request).meta.status()


# ---------------------------------------------------------------------------
# Acciones asesoras (nunca operan; toda salida pasa por el Decision Engine)
# ---------------------------------------------------------------------------


@router.post("/ml/predict")
async def ml_predict(request: Request, context: dict[str, Any]) -> dict[str, Any]:
    """Predicción explicable de calidad para una operación candidata (asesora)."""
    return _engine(request).explain_prediction(context)


@router.post("/ml/train")
async def ml_train(request: Request) -> dict[str, Any]:
    """Entrenamiento bajo demanda (AutoML → validación → registro).

    Seguridad: nunca activa un modelo sin superar la puerta de validación, y sólo
    lo autoactiva si ``auto_activate`` está encendido. Sigue siendo paper trading.
    """
    return await _engine(request).run_nightly_training(author="dashboard")


@router.post("/ml/drift/check")
async def ml_drift_check(request: Request) -> dict[str, Any]:
    """Chequeo de deriva bajo demanda (alerta y reduce confianza si procede)."""
    return await _engine(request).run_drift_check()


@router.post("/ml/meta/evaluate")
async def ml_meta_evaluate(request: Request) -> dict[str, Any]:
    """Ciclo de gobierno del Meta Strategy Manager (ajusta pesos/activación)."""
    return await _engine(request).run_meta_evaluation()

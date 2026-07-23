"""Registro inmutable de un modelo (metadatos completos, Fase 7).

Cada modelo entrenado deja una ficha: ID, versión, fecha, dataset, parámetros,
métricas, estado, autor, resultado y si está activo. El registro nunca borra ni
sobrescribe versiones: sólo cambia de estado y publica versiones nuevas.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.utils.time import utc_now


class ModelState(StrEnum):
    """Ciclo de vida de un modelo en el registro."""

    TRAINING = "training"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    ACTIVE = "active"
    ARCHIVED = "archived"
    REJECTED = "rejected"


@dataclass(slots=True)
class ModelRecord:
    """Metadata card for a single trained model version.

    Attributes:
        id: Identificador único.
        version: Versión incremental por tipo de modelo (``1.0``, ``2.0``...).
        model_type: Tipo del modelo.
        created_at: Fecha de creación (ISO-8601 UTC).
        dataset: Resumen del dataset de entrenamiento.
        params: Hiperparámetros del modelo.
        metrics: Métricas de validación.
        state: Estado actual en el ciclo de vida.
        author: Quién/qué originó el entrenamiento.
        result: Notas del resultado (motivos de aprobación/rechazo).
        active: Si es el modelo activo (asesor, nunca decide por sí solo).
        feature_names: Esquema de features con el que se entrenó.
    """

    id: str
    version: str
    model_type: str
    dataset: dict[str, Any]
    params: dict[str, Any]
    metrics: dict[str, float]
    state: ModelState = ModelState.EVALUATED
    author: str = "system"
    result: str = ""
    active: bool = False
    feature_names: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: utc_now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "id": self.id,
            "version": self.version,
            "model_type": self.model_type,
            "created_at": self.created_at,
            "dataset": self.dataset,
            "params": self.params,
            "metrics": self.metrics,
            "state": self.state.value,
            "author": self.author,
            "result": self.result,
            "active": self.active,
            "feature_names": self.feature_names,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRecord":
        """Rebuild a record from :meth:`to_dict` output."""
        return cls(
            id=str(data["id"]),
            version=str(data.get("version", "1.0")),
            model_type=str(data.get("model_type", "unknown")),
            dataset=dict(data.get("dataset", {})),
            params=dict(data.get("params", {})),
            metrics={k: float(v) for k, v in dict(data.get("metrics", {})).items()},
            state=ModelState(data.get("state", "evaluated")),
            author=str(data.get("author", "system")),
            result=str(data.get("result", "")),
            active=bool(data.get("active", False)),
            feature_names=list(data.get("feature_names", [])),
            created_at=str(data.get("created_at", utc_now().isoformat())),
        )

"""Infraestructura AutoML (Fase 6) — estructura preparada, sin entrenar.

Declara los tipos de modelo que el laboratorio soportará (Random Forest,
XGBoost, LightGBM, CatBoost, redes neuronales, voting y stacking) tras una
interfaz común, pero **no entrena nada todavía**: entrenar modelos complejos
queda para la Fase 7. Cualquier intento de entrenar falla con un mensaje claro.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ModelType(StrEnum):
    """Tipos de modelo previstos para el AutoML."""

    RANDOM_FOREST = "random_forest"
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"
    CATBOOST = "catboost"
    NEURAL_NET = "neural_net"
    VOTING = "voting"
    STACKING = "stacking"


class AutoMLNotEnabledError(RuntimeError):
    """AutoML training is intentionally disabled until Phase 7."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Especificación de un modelo candidato (aún sin entrenar)."""

    model_type: ModelType
    hyperparameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {"model_type": self.model_type.value, "hyperparameters": self.hyperparameters}


class AutoMLRegistry:
    """Catalogue of prepared model types (no training in Phase 6)."""

    def available(self) -> list[str]:
        """Model types the infrastructure is prepared for."""
        return [model.value for model in ModelType]

    def register(self, model_type: ModelType, **hyperparameters: Any) -> ModelSpec:
        """Register a model candidate specification (metadata only)."""
        return ModelSpec(model_type=model_type, hyperparameters=dict(hyperparameters))

    def train(self, spec: ModelSpec) -> None:
        """Train a model (disabled in Phase 6).

        Args:
            spec: Especificación del modelo.

        Raises:
            AutoMLNotEnabledError: Siempre; el entrenamiento llega en la Fase 7.
        """
        raise AutoMLNotEnabledError(
            f"El entrenamiento de '{spec.model_type.value}' está deshabilitado hasta la Fase 7 "
            "(infraestructura preparada, sin modelos entrenados)."
        )

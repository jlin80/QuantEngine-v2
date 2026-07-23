"""Servicio de inferencia: predice usando el modelo activo del registro (Fase 7).

Es el punto por el que el resto del sistema pide una opinión al ML sobre una
operación candidata. Usa el modelo activo del ``ModelRegistry``, aplica el factor
de confianza vigente (reducido si hay deriva) y devuelve una predicción
explicable. Si no hay modelo activo, degrada con elegancia: el sistema sigue
operando sólo con reglas.
"""

from collections.abc import Mapping
from typing import Any

from app.ml.features.engineer import FeatureEngineer
from app.ml.prediction.predictor import Prediction, TradeQualityPredictor
from app.ml.registry.registry import ModelRegistry


class InferenceService:
    """Serve explainable predictions from the active registered model.

    Args:
        registry: Registro de modelos (fuente del modelo activo).
        engineer: Constructor de features (mismo esquema del entrenamiento).
    """

    def __init__(self, registry: ModelRegistry, engineer: FeatureEngineer | None = None) -> None:
        self._registry = registry
        self._engineer = engineer or FeatureEngineer()
        self._predictor = TradeQualityPredictor(self._engineer)
        self._confidence_factor = 1.0

    def set_confidence_factor(self, factor: float) -> None:
        """Set the global confidence multiplier (reduced when drift is present)."""
        self._confidence_factor = max(0.0, min(1.0, factor))

    @property
    def confidence_factor(self) -> float:
        """Current confidence multiplier applied to predictions."""
        return self._confidence_factor

    @property
    def has_active_model(self) -> bool:
        """Whether an active model is available to serve predictions."""
        return self._registry.active_model() is not None

    def predict(self, context: Mapping[str, Any]) -> Prediction:
        """Predict trade quality for a candidate-trade context (explained)."""
        model = self._registry.active_model()
        record = self._registry.active_record()
        if model is None or record is None:
            return Prediction.unavailable()
        return self._predictor.predict(
            model,
            context,
            model_id=record.id,
            confidence_factor=self._confidence_factor,
        )

    def status(self) -> dict[str, Any]:
        """Compact inference status for the dashboard."""
        record = self._registry.active_record()
        return {
            "has_active_model": self.has_active_model,
            "confidence_factor": round(self._confidence_factor, 3),
            "active_model": record.to_dict() if record else None,
        }

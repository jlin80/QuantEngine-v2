"""Predicción de calidad de operación con explicación (Fase 7).

El modelo estima P(operación buena). Nunca se devuelve sólo un número: cada
predicción viene con la contribución por variable (qué empuja a favor y en
contra) y una explicación legible. Esto alimenta la IA explicable y al asesor.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.ml.features.engineer import FeatureEngineer
from app.ml.interfaces.model import Model
from app.ml.models.math import clamp


@dataclass(frozen=True, slots=True)
class Prediction:
    """An explainable trade-quality prediction.

    Attributes:
        probability: P(operación buena) según el modelo.
        label: ``good`` | ``bad`` | ``unknown`` (sin modelo activo).
        confidence: Confianza del modelo en la predicción (0-1).
        model_id: Modelo que la produjo (vacío si no hay modelo activo).
        model_type: Tipo del modelo.
        explanation: Contribución firmada por variable (mayores primero).
        reasons: Explicación legible de la recomendación.
    """

    probability: float
    label: str
    confidence: float
    model_id: str
    model_type: str
    explanation: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "probability": round(self.probability, 4),
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "model_id": self.model_id,
            "model_type": self.model_type,
            "explanation": self.explanation,
            "reasons": self.reasons,
        }

    @classmethod
    def unavailable(cls) -> "Prediction":
        """Neutral prediction used when no ML model is active."""
        return cls(
            probability=0.5,
            label="unknown",
            confidence=0.0,
            model_id="",
            model_type="",
            explanation=[],
            reasons=["No hay modelo de ML activo; el sistema opera sólo con reglas."],
        )


class TradeQualityPredictor:
    """Turn a model + context into an explainable :class:`Prediction`.

    Args:
        engineer: Constructor de features (mismo esquema del entrenamiento).
    """

    def __init__(self, engineer: FeatureEngineer | None = None) -> None:
        self._engineer = engineer or FeatureEngineer()

    def predict(
        self,
        model: Model,
        context: Mapping[str, Any],
        *,
        model_id: str = "",
        top_k: int = 4,
        confidence_factor: float = 1.0,
    ) -> Prediction:
        """Predict trade quality for a candidate-trade context, explained.

        Args:
            model: Modelo activo.
            context: Contexto de la operación candidata.
            model_id: Id del modelo (para trazabilidad).
            top_k: Cuántas variables explicar.
            confidence_factor: Factor de confianza (p. ej. reducido por deriva).

        Returns:
            La predicción explicable.
        """
        names = self._engineer.feature_names()
        vector = self._engineer.from_context(context)
        probability = model.predict_one(vector)
        contributions = model.contributions(vector)
        ranked = sorted(
            zip(names, contributions, strict=False), key=lambda kv: abs(kv[1]), reverse=True
        )[:top_k]
        explanation = [
            {
                "feature": name,
                "contribution": round(value, 4),
                "direction": "a favor" if value >= 0 else "en contra",
            }
            for name, value in ranked
        ]
        confidence = clamp(2.0 * abs(probability - 0.5) * confidence_factor, 0.0, 1.0)
        return Prediction(
            probability=probability,
            label="good" if probability >= 0.5 else "bad",
            confidence=confidence,
            model_id=model_id,
            model_type=model.model_type.value,
            explanation=explanation,
            reasons=_reasons(probability, ranked),
        )


def _reasons(probability: float, ranked: list[tuple[str, float]]) -> list[str]:
    """Build human-readable reasons from the top contributions."""
    verdict = (
        "El modelo ve alta probabilidad de operación buena"
        if probability >= 0.6
        else (
            "El modelo ve baja probabilidad de continuación"
            if probability < 0.4
            else "El modelo es neutral sobre esta operación"
        )
    )
    reasons = [f"{verdict} (P={probability:.0%})."]
    for name, value in ranked[:3]:
        pull = "empuja a favor" if value >= 0 else "resta"
        reasons.append(f"La variable '{name}' {pull} ({value:+.3f}).")
    return reasons

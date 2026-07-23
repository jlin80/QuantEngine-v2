"""Asesor de riesgo por ML (Fase 7) — estima, nunca opera.

Combina la predicción del modelo activo (probabilidad de operación buena) con la
evidencia histórica de operaciones similares (tasa de stop, duración media,
expectativa) para estimar probabilidad de éxito/pérdida/stop, tiempo esperado y
riesgo esperado. **No abre ni cierra posiciones**: toda salida es un consejo
explicable que el Decision Engine y el Risk Manager pueden usar o ignorar.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import MLAdvisorSettings
from app.execution.models.trades import TradeRecord
from app.ml.inference.service import InferenceService
from app.ml.services.stats import TradeStats
from app.ml.services.strategy_intelligence import side_of


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Advisory risk estimate for a candidate trade (never an order)."""

    p_success: float
    p_loss: float
    p_stop: float
    expected_time_seconds: float
    expected_r: float
    sample_size: int
    recommendation: str  # "favorable" | "cauto" | "desfavorable" | "neutral"
    ml_available: bool
    reasons: list[str] = field(default_factory=list)
    explanation: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "p_success": round(self.p_success, 4),
            "p_loss": round(self.p_loss, 4),
            "p_stop": round(self.p_stop, 4),
            "expected_time_seconds": round(self.expected_time_seconds, 1),
            "expected_r": round(self.expected_r, 4),
            "sample_size": self.sample_size,
            "recommendation": self.recommendation,
            "ml_available": self.ml_available,
            "reasons": self.reasons,
            "explanation": self.explanation,
        }


class RiskAdvisor:
    """Estimate trade risk from the model plus similar historical trades.

    Args:
        inference: Servicio de inferencia (modelo activo).
        settings: Umbrales del asesor.
    """

    def __init__(self, inference: InferenceService, settings: MLAdvisorSettings) -> None:
        self._inference = inference
        self._settings = settings

    def assess(
        self, context: Mapping[str, Any], similar_trades: Sequence[TradeRecord]
    ) -> RiskAssessment:
        """Assess a candidate trade using ML and historical evidence."""
        prediction = self._inference.predict(context)
        ml_available = prediction.label != "unknown"
        stats = TradeStats.from_trades(similar_trades)
        p_success = prediction.probability
        reasons = list(prediction.reasons)
        reasons.extend(_history_reasons(stats))
        recommendation = self._recommend(p_success, stats, ml_available)
        return RiskAssessment(
            p_success=p_success,
            p_loss=1.0 - p_success,
            p_stop=stats.stop_rate,
            expected_time_seconds=stats.avg_duration_seconds,
            expected_r=stats.expectancy_r,
            sample_size=stats.trades,
            recommendation=recommendation,
            ml_available=ml_available,
            reasons=reasons,
            explanation=prediction.explanation,
        )

    def _recommend(self, p_success: float, stats: TradeStats, ml_available: bool) -> str:
        """Turn probabilities and evidence into an advisory verdict."""
        if not ml_available and stats.trades < self._settings.min_similar_trades:
            return "neutral"
        if p_success < self._settings.advise_against_below:
            return "desfavorable"
        if p_success < self._settings.low_confidence_below or stats.expectancy_r < 0:
            return "cauto"
        return "favorable"

    @staticmethod
    def filter_similar(
        trades: Sequence[TradeRecord],
        *,
        symbol: str | None = None,
        regime: str | None = None,
        side: str | None = None,
    ) -> list[TradeRecord]:
        """Select historically similar trades (by symbol/regime/side)."""
        result: list[TradeRecord] = []
        for trade in trades:
            if symbol is not None and trade.symbol != symbol.upper():
                continue
            if regime is not None and trade.regime != regime:
                continue
            if side is not None and side_of(trade) != side.lower():
                continue
            result.append(trade)
        return result


def _history_reasons(stats: TradeStats) -> list[str]:
    """Explainable reasons drawn from historical similar trades."""
    if stats.trades == 0:
        return ["No hay operaciones similares en el historial para comparar."]
    return [
        f"{stats.trades} operaciones similares: win rate {stats.win_rate:.0%}, "
        f"expectativa {stats.expectancy_r:+.2f}R, tasa de stop {stats.stop_rate:.0%}."
    ]

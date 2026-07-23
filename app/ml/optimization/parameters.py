"""Recomendación de parámetros a partir de la evidencia (Fase 7).

El ML no reescribe estrategias: sugiere ajustes de configuración razonados a
partir de su rendimiento. Cada recomendación es explicable (motivo + evidencia)
y **nunca** se aplica sola: es material para que el humano o el laboratorio de
backtesting la validen.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ml.services.strategy_intelligence import StrategyScore


@dataclass(frozen=True, slots=True)
class ParameterRecommendation:
    """A single, evidence-backed configuration suggestion."""

    strategy: str
    parameter: str
    action: str  # "increase" | "decrease" | "enable" | "review"
    rationale: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "parameter": self.parameter,
            "action": self.action,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
        }


class ParameterRecommender:
    """Suggest parameter changes from a strategy's measured behaviour.

    Args:
        min_trades: Operaciones mínimas para emitir una recomendación fiable.
    """

    def __init__(self, *, min_trades: int = 20) -> None:
        self._min_trades = min_trades

    def recommend(self, score: StrategyScore) -> list[ParameterRecommendation]:
        """Produce recommendations for one strategy (may be empty)."""
        stats = score.historical
        if stats.trades < self._min_trades:
            return []
        out: list[ParameterRecommendation] = []
        if stats.stop_rate > 0.5 and stats.expectancy_r < 0:
            out.append(
                ParameterRecommendation(
                    strategy=score.name,
                    parameter="atr_stop_multiplier",
                    action="increase",
                    rationale="Alta tasa de stop con expectativa negativa: el stop parece "
                    "demasiado ajustado.",
                    confidence=0.6,
                    evidence={
                        "stop_rate": round(stats.stop_rate, 3),
                        "expectancy_r": round(stats.expectancy_r, 3),
                    },
                )
            )
        if stats.win_rate > 0.55 and stats.profit_factor < 1.2:
            out.append(
                ParameterRecommendation(
                    strategy=score.name,
                    parameter="reward_risk",
                    action="increase",
                    rationale="Buen win rate pero profit factor bajo: los ganadores se cierran "
                    "pronto; subir el objetivo puede mejorar la expectativa.",
                    confidence=0.55,
                    evidence={
                        "win_rate": round(stats.win_rate, 3),
                        "profit_factor": round(stats.profit_factor, 3),
                    },
                )
            )
        if stats.win_rate < 0.4 and score.score < 45:
            out.append(
                ParameterRecommendation(
                    strategy=score.name,
                    parameter="min_score",
                    action="increase",
                    rationale="Baja selectividad: subir el umbral de score reduciría las "
                    "operaciones de baja calidad.",
                    confidence=0.5,
                    evidence={"win_rate": round(stats.win_rate, 3), "score": round(score.score, 1)},
                )
            )
        best_session = _best_group(score.by_session)
        if best_session is not None:
            out.append(
                ParameterRecommendation(
                    strategy=score.name,
                    parameter="allowed_sessions",
                    action="review",
                    rationale=f"El rendimiento se concentra en la sesión '{best_session}': "
                    "considera restringir la estrategia a sus mejores horas.",
                    confidence=0.45,
                    evidence={"by_session": score.by_session},
                )
            )
        return out

    def recommend_all(self, scores: Sequence[StrategyScore]) -> dict[str, list[dict[str, Any]]]:
        """Recommendations for every strategy (JSON-safe)."""
        result: dict[str, list[dict[str, Any]]] = {}
        for score in scores:
            recs = self.recommend(score)
            if recs:
                result[score.name] = [r.to_dict() for r in recs]
        return result


def _best_group(groups: dict[str, dict[str, Any]]) -> str | None:
    """Name of the clearly-best group by expectancy, or ``None`` if diffuse."""
    ranked = sorted(groups.items(), key=lambda kv: kv[1].get("expectancy_r", 0.0), reverse=True)
    if len(ranked) < 2:
        return None
    best, second = ranked[0], ranked[1]
    if best[1].get("expectancy_r", 0.0) - second[1].get("expectancy_r", 0.0) > 0.3:
        return best[0]
    return None

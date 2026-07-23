"""Meta Strategy Manager (Fase 7) — gobierno por encima de estrategias y ML.

Analiza continuamente el rendimiento de todas las estrategias y gestiona su
activación, prioridad y peso. Reduce el peso de las degradadas, sube el de las
consistentes y desactiva las que incumplen los mínimos durante un periodo
configurable. Recomienda combinaciones nuevas para el laboratorio y guarda un
historial de decisiones para auditoría.

Nunca modifica el código de las estrategias: sólo su configuración (activación y
ponderación). Y nunca abre ni cierra posiciones: sus pesos alimentan al consenso,
que sigue pasando por el Decision Engine y el Risk Manager. Solo paper trading.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import MLMetaStrategySettings
from app.ml.models.math import clamp
from app.ml.services.strategy_intelligence import (
    LabeledTrade,
    StrategyIntelligence,
    StrategyScore,
)
from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class MetaReport:
    """Outcome of a meta-strategy evaluation cycle."""

    weights: dict[str, float] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    active: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    ranking: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "decisions": self.decisions,
            "active": self.active,
            "disabled": self.disabled,
            "ranking": self.ranking,
        }


class MetaStrategyManager:
    """Govern strategy activation, priority and weight from live evidence.

    Args:
        settings: Parámetros del gobierno de estrategias.
        intelligence: Motor de puntuación de estrategias.
    """

    def __init__(
        self, settings: MLMetaStrategySettings, intelligence: StrategyIntelligence
    ) -> None:
        self._settings = settings
        self._intelligence = intelligence
        self._weights: dict[str, float] = {}
        self._active: dict[str, bool] = {}
        self._fail_counts: dict[str, int] = {}
        self._audit: list[dict[str, Any]] = []
        self._evaluations = 0

    def evaluate(self, labeled_trades: Sequence[LabeledTrade]) -> MetaReport:
        """Score strategies and adjust their weights/activation."""
        scores = self._intelligence.rank(labeled_trades)
        decisions: list[dict[str, Any]] = []
        for score in scores:
            decisions.append(self._govern(score))
        self._evaluations += 1
        active = [name for name, on in self._active.items() if on]
        disabled = [name for name, on in self._active.items() if not on]
        return MetaReport(
            weights=dict(self._weights),
            decisions=decisions,
            active=sorted(active),
            disabled=sorted(disabled),
            ranking=[s.to_dict() for s in scores],
        )

    def _govern(self, score: StrategyScore) -> dict[str, Any]:
        """Decide activation and weight for a single strategy."""
        name = score.name
        previous_weight = self._weights.get(name, 1.0)
        was_active = self._active.get(name, True)
        degraded = self._is_degraded(score)
        self._fail_counts[name] = self._fail_counts.get(name, 0) + 1 if degraded else 0

        if self._fail_counts[name] >= self._settings.disable_after_periods:
            self._active[name] = False
            self._weights[name] = self._settings.min_weight
            action = "disable"
            detail = f"Degradada {self._fail_counts[name]} evaluaciones seguidas; se desactiva."
        else:
            self._active[name] = True
            target = self._target_weight(score.score)
            new_weight = previous_weight + self._settings.weight_smoothing * (
                target - previous_weight
            )
            self._weights[name] = round(
                clamp(new_weight, self._settings.min_weight, self._settings.max_weight), 4
            )
            action = self._classify(was_active, previous_weight, self._weights[name])
            detail = self._detail(action, score)

        decision = {
            "strategy": name,
            "action": action,
            "weight": self._weights[name],
            "previous_weight": round(previous_weight, 4),
            "active": self._active[name],
            "detail": detail,
            "metrics": {
                "score": round(score.score, 2),
                "trades": score.trades,
                "expectancy_r": round(score.recent.expectancy_r, 4),
                "profit_factor": round(score.recent.profit_factor, 4),
            },
        }
        if action != "keep":
            self._audit.append(
                {"evaluation": self._evaluations, "at": utc_now().isoformat(), **decision}
            )
        return decision

    def _is_degraded(self, score: StrategyScore) -> bool:
        """Whether a strategy currently fails the minimum criteria."""
        if score.trades < self._settings.min_trades or score.recent.trades == 0:
            return False
        return (
            score.recent.expectancy_r < self._settings.disable_expectancy_r
            or score.recent.profit_factor < self._settings.disable_profit_factor
        )

    def _target_weight(self, score_value: float) -> float:
        """Map a 0-100 strategy score to a target weight."""
        span = self._settings.max_weight - self._settings.min_weight
        return self._settings.min_weight + span * (score_value / 100.0)

    @staticmethod
    def _classify(was_active: bool, previous: float, new: float) -> str:
        """Classify the governance action from the weight change."""
        if not was_active:
            return "enable"
        if new > previous + 0.05:
            return "boost"
        if new < previous - 0.05:
            return "reduce"
        return "keep"

    @staticmethod
    def _detail(action: str, score: StrategyScore) -> str:
        """Human-readable detail for a governance action."""
        mapping = {
            "boost": "Rendimiento consistente: se aumenta el peso.",
            "reduce": "Rendimiento a la baja: se reduce el peso.",
            "enable": "Recuperada: se reactiva.",
            "keep": "Sin cambios relevantes.",
        }
        return f"{mapping.get(action, '')} (score {score.score:.0f})"

    def recommend_combinations(
        self, labeled_trades: Sequence[LabeledTrade], *, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Suggest complementary strategy pairs for the backtest lab."""
        scores = self._intelligence.rank(labeled_trades)
        strong = [s for s in scores if s.score >= 55.0 and s.trades >= self._settings.min_trades]
        suggestions: list[dict[str, Any]] = []
        for i, first in enumerate(strong):
            for second in strong[i + 1 :]:
                regime_a = _best_regime(first)
                regime_b = _best_regime(second)
                if regime_a and regime_b and regime_a != regime_b:
                    suggestions.append(
                        {
                            "strategies": [first.name, second.name],
                            "regimes": {first.name: regime_a, second.name: regime_b},
                            "rationale": (
                                f"'{first.name}' rinde en '{regime_a}' y '{second.name}' en "
                                f"'{regime_b}': combinarlas podría cubrir más regímenes."
                            ),
                        }
                    )
                if len(suggestions) >= limit:
                    return suggestions
        return suggestions

    def weights(self) -> dict[str, float]:
        """Current dynamic weight per strategy."""
        return dict(self._weights)

    def active_strategies(self) -> list[str]:
        """Strategies currently kept active."""
        return sorted(name for name, on in self._active.items() if on)

    def history(self) -> list[dict[str, Any]]:
        """Audit trail of governance decisions."""
        return list(self._audit)

    def status(self) -> dict[str, Any]:
        """Compact meta-manager status for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "evaluations": self._evaluations,
            "strategies": len(self._weights),
            "weights": {k: round(v, 4) for k, v in self._weights.items()},
            "active": self.active_strategies(),
            "disabled": sorted(name for name, on in self._active.items() if not on),
        }


def _best_regime(score: StrategyScore) -> str | None:
    """The regime where a strategy performs best (by expectancy)."""
    ranked = sorted(
        score.by_regime.items(), key=lambda kv: kv[1].get("expectancy_r", 0.0), reverse=True
    )
    return ranked[0][0] if ranked else None

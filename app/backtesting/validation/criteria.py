"""Criterios mínimos de calificación (Fase 6).

Traduce los umbrales configurables (``QualificationCriteriaSettings``) en una
lista de chequeos evaluables contra la estadística de un backtest. Ninguna
estrategia se promueve si no supera estos mínimos — la decisión es explícita y
explicada, nunca un booleano opaco.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config.settings import QualificationCriteriaSettings


@dataclass(frozen=True, slots=True)
class CriterionCheck:
    """El resultado de evaluar un criterio concreto."""

    name: str
    passed: bool
    actual: float
    threshold: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "passed": self.passed,
            "actual": round(self.actual, 6),
            "threshold": self.threshold,
            "detail": self.detail,
        }


def evaluate_metrics(
    stats: dict[str, Any], criteria: QualificationCriteriaSettings
) -> list[CriterionCheck]:
    """Evaluate the statistical minimums against a backtest's stats.

    Args:
        stats: Estadística serializada (``QuantStatistics.to_dict``).
        criteria: Umbrales configurados.

    Returns:
        Un chequeo por criterio (rachas, PF, Sharpe, drawdown, SQN...).
    """
    total_trades = float(stats.get("total_trades", 0) or 0)
    profit_factor = float(stats.get("profit_factor", 0.0) or 0.0)
    sharpe = float(stats.get("sharpe", 0.0) or 0.0)
    max_dd = float(stats.get("max_drawdown_pct", 0.0) or 0.0)
    expectancy = float(stats.get("expectancy", 0.0) or 0.0)
    sqn = float(stats.get("sqn", 0.0) or 0.0)
    win_rate = float(stats.get("win_rate", 0.0) or 0.0)
    return [
        CriterionCheck(
            "min_trades",
            total_trades >= criteria.min_trades,
            total_trades,
            criteria.min_trades,
            f"{int(total_trades)} operaciones (mínimo {criteria.min_trades})",
        ),
        CriterionCheck(
            "profit_factor",
            profit_factor >= criteria.min_profit_factor,
            profit_factor,
            criteria.min_profit_factor,
            f"PF {profit_factor:.2f} (mínimo {criteria.min_profit_factor})",
        ),
        CriterionCheck(
            "sharpe",
            sharpe >= criteria.min_sharpe,
            sharpe,
            criteria.min_sharpe,
            f"Sharpe {sharpe:.2f} (mínimo {criteria.min_sharpe})",
        ),
        CriterionCheck(
            "max_drawdown",
            max_dd <= criteria.max_drawdown_pct,
            max_dd,
            criteria.max_drawdown_pct,
            f"Drawdown {max_dd:.2f}% (máximo {criteria.max_drawdown_pct}%)",
        ),
        CriterionCheck(
            "expectancy",
            expectancy > criteria.min_expectancy,
            expectancy,
            criteria.min_expectancy,
            f"Expectativa {expectancy:.4f} (mínimo > {criteria.min_expectancy})",
        ),
        CriterionCheck(
            "sqn",
            sqn >= criteria.min_sqn,
            sqn,
            criteria.min_sqn,
            f"SQN {sqn:.2f} (mínimo {criteria.min_sqn})",
        ),
        CriterionCheck(
            "win_rate",
            win_rate >= criteria.min_win_rate,
            win_rate,
            criteria.min_win_rate,
            f"Win rate {win_rate:.2%} (mínimo {criteria.min_win_rate:.2%})",
        ),
    ]


def all_passed(checks: Sequence[CriterionCheck]) -> bool:
    """Whether every check passed."""
    return all(check.passed for check in checks)

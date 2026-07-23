"""Simulación Monte Carlo del laboratorio (Fase 6).

Reordena/remuestrea la secuencia de resultados por operación para estimar la
distribución de resultados, la robustez, el drawdown esperado, el capital
mínimo, el riesgo de ruina y los intervalos de confianza. Responde a la
pregunta "¿este resultado fue habilidad o suerte del orden de las operaciones?".
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config.settings import MonteCarloSettings


def _percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile of a sample.

    Args:
        values: Muestra (no necesita estar ordenada).
        pct: Percentil en [0, 100].

    Returns:
        El valor del percentil (0.0 si la muestra está vacía).
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """Aggregate outcome of a Monte Carlo study."""

    simulations: int
    initial_capital: float
    mean_return_pct: float
    median_return_pct: float
    std_return_pct: float
    return_ci_low_pct: float
    return_ci_high_pct: float
    percentile_5_return_pct: float
    percentile_95_return_pct: float
    expected_max_drawdown_pct: float
    worst_max_drawdown_pct: float
    required_capital_pct: float
    risk_of_ruin: float
    profitable_probability: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "simulations": self.simulations,
            "initial_capital": self.initial_capital,
            "mean_return_pct": round(self.mean_return_pct, 4),
            "median_return_pct": round(self.median_return_pct, 4),
            "std_return_pct": round(self.std_return_pct, 4),
            "return_ci_low_pct": round(self.return_ci_low_pct, 4),
            "return_ci_high_pct": round(self.return_ci_high_pct, 4),
            "percentile_5_return_pct": round(self.percentile_5_return_pct, 4),
            "percentile_95_return_pct": round(self.percentile_95_return_pct, 4),
            "expected_max_drawdown_pct": round(self.expected_max_drawdown_pct, 4),
            "worst_max_drawdown_pct": round(self.worst_max_drawdown_pct, 4),
            "required_capital_pct": round(self.required_capital_pct, 4),
            "risk_of_ruin": round(self.risk_of_ruin, 4),
            "profitable_probability": round(self.profitable_probability, 4),
        }


class MonteCarloSimulator:
    """Resample per-trade PnLs to estimate the distribution of outcomes.

    Args:
        settings: Parámetros Monte Carlo (nº de simulaciones, método, ruina).
    """

    def __init__(self, settings: MonteCarloSettings) -> None:
        self._settings = settings

    def run(self, trade_pnls: Sequence[float]) -> MonteCarloResult:
        """Run the Monte Carlo study over a sequence of trade PnLs.

        Args:
            trade_pnls: PnL neto por operación, en la divisa de la cuenta.

        Returns:
            Distribución agregada de resultados y métricas de robustez.
        """
        capital = self._settings.initial_capital
        empty = MonteCarloResult(
            simulations=0,
            initial_capital=capital,
            mean_return_pct=0.0,
            median_return_pct=0.0,
            std_return_pct=0.0,
            return_ci_low_pct=0.0,
            return_ci_high_pct=0.0,
            percentile_5_return_pct=0.0,
            percentile_95_return_pct=0.0,
            expected_max_drawdown_pct=0.0,
            worst_max_drawdown_pct=0.0,
            required_capital_pct=0.0,
            risk_of_ruin=0.0,
            profitable_probability=0.0,
        )
        if not trade_pnls or capital <= 0:
            return empty

        rng = random.Random(self._settings.random_seed)
        pnls = list(trade_pnls)
        ruin_level = capital * (1.0 - self._settings.ruin_threshold_pct / 100.0)
        returns_pct: list[float] = []
        drawdowns_pct: list[float] = []
        ruined = 0
        for _ in range(self._settings.simulations):
            path = self._sample(pnls, rng)
            final, max_dd_pct, hit_ruin = self._simulate_path(capital, path, ruin_level)
            returns_pct.append((final - capital) / capital * 100.0)
            drawdowns_pct.append(max_dd_pct)
            ruined += int(hit_ruin)

        n = len(returns_pct)
        mean = math.fsum(returns_pct) / n
        variance = math.fsum((r - mean) ** 2 for r in returns_pct) / n
        std = math.sqrt(variance)
        tail = (1.0 - self._settings.confidence) / 2.0 * 100.0
        return MonteCarloResult(
            simulations=n,
            initial_capital=capital,
            mean_return_pct=mean,
            median_return_pct=_percentile(returns_pct, 50.0),
            std_return_pct=std,
            return_ci_low_pct=_percentile(returns_pct, tail),
            return_ci_high_pct=_percentile(returns_pct, 100.0 - tail),
            percentile_5_return_pct=_percentile(returns_pct, 5.0),
            percentile_95_return_pct=_percentile(returns_pct, 95.0),
            expected_max_drawdown_pct=_percentile(drawdowns_pct, 50.0),
            worst_max_drawdown_pct=max(drawdowns_pct),
            required_capital_pct=_percentile(drawdowns_pct, 95.0),
            risk_of_ruin=ruined / n,
            profitable_probability=sum(1 for r in returns_pct if r > 0) / n,
        )

    def _sample(self, pnls: list[float], rng: random.Random) -> list[float]:
        """Produce one resampled/shuffled PnL sequence."""
        if self._settings.method == "shuffle":
            shuffled = list(pnls)
            rng.shuffle(shuffled)
            return shuffled
        return [rng.choice(pnls) for _ in pnls]

    @staticmethod
    def _simulate_path(
        capital: float, pnls: Sequence[float], ruin_level: float
    ) -> tuple[float, float, bool]:
        """Walk an equity path, returning (final, max drawdown %, hit ruin)."""
        equity = capital
        peak = capital
        max_dd_pct = 0.0
        ruined = False
        for pnl in pnls:
            equity += pnl
            if equity <= ruin_level:
                ruined = True
            peak = max(peak, equity)
            if peak > 0:
                max_dd_pct = max(max_dd_pct, (peak - equity) / peak * 100.0)
        return equity, max_dd_pct, ruined

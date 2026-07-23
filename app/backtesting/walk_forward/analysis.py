"""Walk Forward Analysis: optimizar in-sample, validar out-of-sample (Fase 6).

Para cada pliegue: optimiza los parámetros sobre el tramo de entrenamiento y
mide el rendimiento con esos parámetros sobre el tramo de validación que la
optimización no vio. Agrega los resultados out-of-sample y calcula la
estabilidad (consistencia entre pliegues) y la eficiencia (OOS frente a IS).
Es la prueba de fuego contra el sobreajuste temporal.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.backtesting.decisions import DecisionSource
from app.backtesting.engine import BacktestEngine
from app.backtesting.models import BacktestConfig
from app.backtesting.optimizer import Optimizer, ParameterSpace
from app.backtesting.walk_forward.windows import Window, generate_windows
from app.market.models import Candle

SourceFactory = Callable[[dict[str, Any]], DecisionSource]


def objective_value(stats: dict[str, Any], name: str, *, default: float = float("-inf")) -> float:
    """Extract a numeric objective from a statistics dict.

    Args:
        stats: Estadística serializada (``QuantStatistics.to_dict``).
        name: Nombre de la métrica objetivo.
        default: Valor si la métrica falta o no es numérica.

    Returns:
        El valor de la métrica, o ``default``.
    """
    value = stats.get(name)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """Resultado de un pliegue de walk-forward."""

    window: Window
    best_params: dict[str, Any]
    in_sample_score: float
    out_of_sample_return_pct: float
    out_of_sample_stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (sin la estadística OOS completa, para compacidad)."""
        return {
            "window": self.window.to_dict(),
            "best_params": self.best_params,
            "in_sample_score": round(self.in_sample_score, 6),
            "out_of_sample_return_pct": round(self.out_of_sample_return_pct, 4),
            "out_of_sample_sharpe": self.out_of_sample_stats.get("sharpe"),
            "out_of_sample_profit_factor": self.out_of_sample_stats.get("profit_factor"),
        }


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    """Aggregate walk-forward result."""

    scheme: str
    objective: str
    folds: list[WalkForwardFold]
    total_out_of_sample_return_pct: float
    average_out_of_sample_return_pct: float
    positive_fold_ratio: float
    stability: float
    efficiency: float

    @property
    def is_stable(self) -> bool:
        """Heurística: mayoría de pliegues OOS positivos y estabilidad decente."""
        return self.positive_fold_ratio >= 0.6 and self.stability >= 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "scheme": self.scheme,
            "objective": self.objective,
            "folds": [fold.to_dict() for fold in self.folds],
            "total_out_of_sample_return_pct": round(self.total_out_of_sample_return_pct, 4),
            "average_out_of_sample_return_pct": round(self.average_out_of_sample_return_pct, 4),
            "positive_fold_ratio": round(self.positive_fold_ratio, 4),
            "stability": round(self.stability, 4),
            "efficiency": round(self.efficiency, 4),
            "is_stable": self.is_stable,
        }


class WalkForwardAnalysis:
    """Run walk-forward optimization and validation over a candle series.

    Args:
        engine: Motor de backtest (reutiliza el Execution Engine).
        optimizer: Optimizador para el tramo in-sample.
        space: Espacio de parámetros a optimizar.
        source_factory: Construye una fuente de decisiones desde un parámetro.
        objective: Métrica a maximizar in-sample (clave de la estadística).
    """

    def __init__(
        self,
        engine: BacktestEngine,
        optimizer: Optimizer,
        space: ParameterSpace,
        source_factory: SourceFactory,
        *,
        objective: str = "sharpe",
    ) -> None:
        self._engine = engine
        self._optimizer = optimizer
        self._space = space
        self._source_factory = source_factory
        self._objective = objective

    def run(
        self,
        candles: Sequence[Candle],
        config: BacktestConfig,
        *,
        scheme: str = "rolling",
        train_size: int,
        validation_size: int,
        step: int,
    ) -> WalkForwardReport:
        """Run the full walk-forward analysis.

        Args:
            candles: Serie completa de velas.
            config: Configuración base del backtest.
            scheme: Esquema de ventanas (rolling/expanding/anchored).
            train_size: Velas de entrenamiento.
            validation_size: Velas de validación.
            step: Avance entre pliegues.

        Returns:
            El informe agregado de walk-forward.
        """
        windows = generate_windows(
            len(candles),
            scheme=scheme,
            train_size=train_size,
            validation_size=validation_size,
            step=step,
        )
        folds = [self._run_fold(window, candles, config) for window in windows]
        return self._aggregate(scheme, folds)

    def _run_fold(
        self, window: Window, candles: Sequence[Candle], config: BacktestConfig
    ) -> WalkForwardFold:
        """Optimize on the train slice and validate on the test slice."""
        train = candles[window.train_start : window.train_end]
        test = candles[window.test_start : window.test_end]

        def evaluate(params: dict[str, Any]) -> float:
            result = self._engine.run(train, self._source_factory(params), config)
            return objective_value(result.statistics, self._objective)

        optimization = self._optimizer.optimize(self._space, evaluate)
        oos = self._engine.run(test, self._source_factory(optimization.best_params), config)
        return WalkForwardFold(
            window=window,
            best_params=optimization.best_params,
            in_sample_score=optimization.best_score,
            out_of_sample_return_pct=oos.return_pct,
            out_of_sample_stats=oos.statistics,
        )

    def _aggregate(self, scheme: str, folds: Sequence[WalkForwardFold]) -> WalkForwardReport:
        """Aggregate fold results into stability and efficiency metrics."""
        if not folds:
            return WalkForwardReport(scheme, self._objective, [], 0.0, 0.0, 0.0, 0.0, 0.0)
        oos_returns = [fold.out_of_sample_return_pct for fold in folds]
        total = math.fsum(oos_returns)
        average = total / len(oos_returns)
        positive = sum(1 for r in oos_returns if r > 0) / len(oos_returns)
        stability = self._stability(oos_returns, average)
        efficiency = self._efficiency(folds)
        return WalkForwardReport(
            scheme=scheme,
            objective=self._objective,
            folds=list(folds),
            total_out_of_sample_return_pct=total,
            average_out_of_sample_return_pct=average,
            positive_fold_ratio=positive,
            stability=stability,
            efficiency=efficiency,
        )

    @staticmethod
    def _stability(returns: Sequence[float], mean: float) -> float:
        """Stability = 1 - coefficient of variation, clamped to [-1, 1]."""
        if len(returns) < 2:
            return 1.0
        variance = math.fsum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance)
        if abs(mean) < 1e-9:
            return 0.0
        cv = std / abs(mean)
        return max(-1.0, min(1.0, 1.0 - cv))

    @staticmethod
    def _efficiency(folds: Sequence[WalkForwardFold]) -> float:
        """Walk-forward efficiency: mean OOS return over mean IS score (bounded)."""
        is_scores = [f.in_sample_score for f in folds if math.isfinite(f.in_sample_score)]
        oos = [f.out_of_sample_return_pct for f in folds]
        if not is_scores or not oos:
            return 0.0
        mean_is = math.fsum(is_scores) / len(is_scores)
        mean_oos = math.fsum(oos) / len(oos)
        if abs(mean_is) < 1e-9:
            return 0.0
        return mean_oos / mean_is

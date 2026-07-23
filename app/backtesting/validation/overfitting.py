"""Detector de sobreoptimización (Fase 6).

Analiza el resultado de una optimización (y opcionalmente el contraste
in-sample vs out-of-sample) para advertir de sobreajuste, data snooping, curve
fitting, inestabilidad, sensibilidad extrema y parámetros irreales. No decide
por sí solo: genera advertencias con severidad y un índice de riesgo [0, 1] que
el pipeline de calificación tiene en cuenta.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.backtesting.optimizer import OptimizationResult
from app.backtesting.optimizer.space import ParameterSpace


@dataclass(frozen=True, slots=True)
class OverfittingWarning:
    """Una advertencia concreta de sobreajuste."""

    category: str
    severity: str  # info | warning | critical
    message: str

    def to_dict(self) -> dict[str, str]:
        """JSON-safe dict."""
        return {"category": self.category, "severity": self.severity, "message": self.message}


@dataclass(frozen=True, slots=True)
class OverfittingReport:
    """Resultado del análisis de sobreajuste."""

    risk: float
    warnings: list[OverfittingWarning]

    @property
    def is_overfit(self) -> bool:
        """Heurística: riesgo alto o alguna advertencia crítica."""
        return self.risk >= 0.5 or any(w.severity == "critical" for w in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "risk": round(self.risk, 4),
            "is_overfit": self.is_overfit,
            "warnings": [w.to_dict() for w in self.warnings],
        }


class OverfittingDetector:
    """Assess overfitting risk from an optimization and IS/OOS contrast.

    Args:
        outlier_sigmas: Sigmas sobre la media para marcar el best como outlier.
        degradation_ratio: OOS/IS por debajo del cual se marca degradación.
    """

    def __init__(self, *, outlier_sigmas: float = 3.0, degradation_ratio: float = 0.5) -> None:
        self._outlier_sigmas = outlier_sigmas
        self._degradation_ratio = degradation_ratio

    def assess(
        self,
        optimization: OptimizationResult,
        *,
        space: ParameterSpace | None = None,
        in_sample_score: float | None = None,
        out_of_sample_score: float | None = None,
    ) -> OverfittingReport:
        """Assess overfitting risk.

        Args:
            optimization: Resultado de la optimización.
            space: Espacio de parámetros (para detectar valores en el borde).
            in_sample_score: Score in-sample del mejor conjunto (opcional).
            out_of_sample_score: Score out-of-sample del mejor conjunto (opcional).

        Returns:
            Informe con advertencias y un índice de riesgo agregado.
        """
        warnings: list[OverfittingWarning] = []
        risk = 0.0
        risk += self._check_outlier(optimization, warnings)
        risk += self._check_degradation(in_sample_score, out_of_sample_score, warnings)
        risk += self._check_snooping(optimization, warnings)
        if space is not None:
            risk += self._check_boundaries(optimization, space, warnings)
        return OverfittingReport(risk=min(1.0, risk), warnings=warnings)

    def _check_outlier(
        self, optimization: OptimizationResult, warnings: list[OverfittingWarning]
    ) -> float:
        """Flag a best score that is a statistical outlier (curve fitting)."""
        scores = [t.score for t in optimization.trials if math.isfinite(t.score)]
        if len(scores) < 5:
            return 0.0
        mean = math.fsum(scores) / len(scores)
        variance = math.fsum((s - mean) ** 2 for s in scores) / len(scores)
        std = math.sqrt(variance)
        if std <= 0:
            return 0.0
        z = (optimization.best_score - mean) / std
        if z >= self._outlier_sigmas:
            warnings.append(
                OverfittingWarning(
                    "curve_fitting",
                    "warning",
                    f"El mejor score está {z:.1f}σ sobre la media: posible curva ajustada "
                    "a una combinación singular.",
                )
            )
            return 0.3
        return 0.0

    def _check_degradation(
        self,
        in_sample: float | None,
        out_of_sample: float | None,
        warnings: list[OverfittingWarning],
    ) -> float:
        """Flag out-of-sample degradation relative to in-sample."""
        if in_sample is None or out_of_sample is None:
            return 0.0
        if in_sample <= 0:
            return 0.0
        ratio = out_of_sample / in_sample
        if ratio < self._degradation_ratio:
            severity = "critical" if ratio <= 0 else "warning"
            warnings.append(
                OverfittingWarning(
                    "data_snooping",
                    severity,
                    f"El rendimiento out-of-sample es {ratio:.0%} del in-sample: "
                    "los parámetros no generalizan.",
                )
            )
            return 0.4 if severity == "critical" else 0.3
        return 0.0

    def _check_snooping(
        self, optimization: OptimizationResult, warnings: list[OverfittingWarning]
    ) -> float:
        """Flag an excessive number of evaluations (multiple comparisons)."""
        if optimization.evaluations >= 500:
            warnings.append(
                OverfittingWarning(
                    "data_snooping",
                    "info",
                    f"{optimization.evaluations} evaluaciones: a más pruebas, más fácil "
                    "encontrar un buen resultado por azar. Exigir validación out-of-sample.",
                )
            )
            return 0.1
        return 0.0

    @staticmethod
    def _check_boundaries(
        optimization: OptimizationResult,
        space: ParameterSpace,
        warnings: list[OverfittingWarning],
    ) -> float:
        """Flag best parameters sitting on the boundary of a continuous range."""
        risk = 0.0
        for spec in space.specs:
            if spec.low is None or spec.high is None:
                continue
            value = optimization.best_params.get(spec.name)
            if not isinstance(value, int | float):
                continue
            span = spec.high - spec.low
            if span <= 0:
                continue
            near_low = abs(value - spec.low) <= 0.02 * span
            near_high = abs(value - spec.high) <= 0.02 * span
            if near_low or near_high:
                warnings.append(
                    OverfittingWarning(
                        "unrealistic_params",
                        "warning",
                        f"El óptimo de '{spec.name}' cae en el borde del rango: amplía el "
                        "dominio o sospecha de un artefacto.",
                    )
                )
                risk += 0.1
        return min(0.3, risk)


def robustness_by_chunks(returns_per_chunk: Sequence[float]) -> float:
    """Fraction of contiguous chunks with a positive return.

    Args:
        returns_per_chunk: Retorno de cada tramo contiguo del backtest.

    Returns:
        Fracción de tramos rentables (proxy de robustez temporal).
    """
    if not returns_per_chunk:
        return 0.0
    return sum(1 for r in returns_per_chunk if r > 0) / len(returns_per_chunk)

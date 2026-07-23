"""Validación estadística y calificación de estrategias (Fase 6)."""

from app.backtesting.validation.criteria import CriterionCheck, all_passed, evaluate_metrics
from app.backtesting.validation.overfitting import (
    OverfittingDetector,
    OverfittingReport,
    OverfittingWarning,
    robustness_by_chunks,
)
from app.backtesting.validation.qualification import (
    QualificationReport,
    StrategyQualificationPipeline,
)

__all__ = [
    "CriterionCheck",
    "OverfittingDetector",
    "OverfittingReport",
    "OverfittingWarning",
    "QualificationReport",
    "StrategyQualificationPipeline",
    "all_passed",
    "evaluate_metrics",
    "robustness_by_chunks",
]

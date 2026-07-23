"""Promotion Manager fail-closed (Fase 10)."""

from collections.abc import Mapping

from app.config.settings import PromotionSettings
from app.research.genetic_optimizer import scalarize
from app.research.models import (
    CandidateReport,
    PaperTrialStatus,
    PromotionDecision,
    StrategyGenome,
)


class PromotionManager:
    """Decide whether a candidate may be promoted (never enables live).

    Regla: *fail-closed*. Ante cualquier requisito no cumplido, no promueve y
    registra el motivo. Aunque apruebe, sólo declara la candidata como promovida
    a nivel de investigación; el paso a live sigue vetado por la Fase 9.

    Args:
        settings: Requisitos de promoción.
        objectives: Pesos multiobjetivo para comparar con la vigente.
    """

    def __init__(self, settings: PromotionSettings, objectives: Mapping[str, float]) -> None:
        self._settings = settings
        self._objectives = dict(objectives)

    def evaluate(
        self,
        genome: StrategyGenome,
        report: CandidateReport,
        *,
        paper_status: PaperTrialStatus | None,
        current_metrics: Mapping[str, float] | None,
        drift: float,
        operator_approved: bool,
        operator: str = "",
    ) -> PromotionDecision:
        """Judge a promotion, returning a fully-explained decision.

        Args:
            genome: Genoma candidato.
            report: Informe del pipeline (debe haber pasado).
            paper_status: Estado de la validación en paper.
            current_metrics: Métricas de la estrategia vigente (o ``None``).
            drift: Deriva medida de las features (tipo PSI).
            operator_approved: Si el operador aprobó explícitamente.
            operator: Actor de la decisión (para la auditoría).

        Returns:
            La decisión de promoción con motivos y bloqueos.
        """
        reasons: list[str] = []
        blockers: list[str] = []

        if report.passed:
            reasons.append("pipeline de validación superado")
        else:
            blockers.append("no superó el pipeline de validación")

        if paper_status is not None and paper_status.matured:
            reasons.append("validación paper madura")
        else:
            blockers.append("validación paper no completada")

        if drift <= self._settings.max_drift:
            reasons.append(
                f"sin drift significativo ({drift:.2f} ≤ {self._settings.max_drift:.2f})"
            )
        else:
            blockers.append(f"drift {drift:.2f} > {self._settings.max_drift:.2f}")

        improvement = self._improvement(report, current_metrics)
        if self._settings.require_beat_current and current_metrics:
            if improvement >= self._settings.min_improvement:
                reasons.append(f"supera a la vigente (mejora {improvement:.3f})")
            else:
                blockers.append(
                    f"no supera a la vigente (mejora {improvement:.3f} < "
                    f"{self._settings.min_improvement:.3f})"
                )
        elif not current_metrics:
            reasons.append("no hay estrategia vigente que superar")

        if self._settings.require_operator_approval:
            if operator_approved:
                reasons.append("aprobación del operador registrada")
            else:
                blockers.append("falta la aprobación explícita del operador")

        approved = not blockers
        return PromotionDecision(
            genome_id=genome.id,
            name=genome.name,
            approved=approved,
            operator=operator,
            drift=drift,
            improvement=improvement,
            reasons=tuple(reasons),
            blockers=tuple(blockers),
        )

    def _improvement(
        self, report: CandidateReport, current_metrics: Mapping[str, float] | None
    ) -> float:
        """Composite-score improvement of the candidate over the incumbent."""
        challenger = scalarize(report.statistics, self._objectives)
        if not current_metrics:
            return challenger
        return challenger - scalarize(current_metrics, self._objectives)

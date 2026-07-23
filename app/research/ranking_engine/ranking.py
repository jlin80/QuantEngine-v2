"""Clasificación de estrategias por evidencia (Fase 10)."""

from collections.abc import Mapping, Sequence

from app.research.genetic_optimizer import scalarize
from app.research.models import CandidateReport, RankingEntry

# Métricas destacadas que acompañan cada fila del ranking.
_HEADLINE = ("profit_factor", "sharpe", "expectancy_r", "max_drawdown_pct", "stability", "sqn")


class RankingEngine:
    """Rank candidate reports by a composite multi-objective score.

    Args:
        objectives: Pesos por objetivo (los mismos del optimizador multiobjetivo).
    """

    def __init__(self, objectives: Mapping[str, float]) -> None:
        self._objectives = dict(objectives)

    def score(self, statistics: Mapping[str, float]) -> float:
        """Composite score of a statistics dict."""
        return scalarize(statistics, self._objectives)

    def rank(
        self, reports: Sequence[CandidateReport], *, segment: str = "global"
    ) -> list[RankingEntry]:
        """Rank reports (highest composite score first).

        Args:
            reports: Informes de candidatas a ordenar.
            segment: Etiqueta del segmento (activo/sesión/régimen/volatilidad).

        Returns:
            Las filas del ranking con su posición y métricas destacadas.
        """
        scored = sorted(reports, key=lambda r: self.score(r.statistics), reverse=True)
        entries: list[RankingEntry] = []
        for i, report in enumerate(scored):
            entries.append(
                RankingEntry(
                    rank=i + 1,
                    genome_id=report.genome_id,
                    name=report.name,
                    segment=segment,
                    score=self.score(report.statistics),
                    metrics={k: report.statistics[k] for k in _HEADLINE if k in report.statistics},
                )
            )
        return entries

    def by_symbol(self, reports: Sequence[CandidateReport]) -> dict[str, list[RankingEntry]]:
        """Rank reports grouped by symbol."""
        groups: dict[str, list[CandidateReport]] = {}
        for report in reports:
            groups.setdefault(report.symbol, []).append(report)
        return {symbol: self.rank(group, segment=symbol) for symbol, group in groups.items()}

    def by_dimension(
        self, reports: Sequence[CandidateReport], dimension: str
    ) -> dict[str, list[RankingEntry]]:
        """Rank reports grouped by a genome-metadata dimension.

        Args:
            reports: Informes a segmentar.
            dimension: Clave (p. ej. ``theme``/``polarity``/``timeframe``);
                se lee de ``statistics`` si está presente, si no de un genérico.

        Returns:
            Un ranking por cada valor distinto de la dimensión.
        """
        groups: dict[str, list[CandidateReport]] = {}
        for report in reports:
            key = str(report.statistics.get(dimension, "n/a"))
            groups.setdefault(key, []).append(report)
        return {
            value: self.rank(group, segment=f"{dimension}={value}")
            for value, group in groups.items()
        }

    def best(self, reports: Sequence[CandidateReport]) -> CandidateReport | None:
        """The single highest-scoring report (or ``None`` when empty)."""
        if not reports:
            return None
        return max(reports, key=lambda r: self.score(r.statistics))

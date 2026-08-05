"""Informe de salud del edge — una fila por estrategia, más el agregado.

Todos los campos son primitivos JSON-safe: el informe viaja al Event Bus, al
histórico en disco y al dashboard sin adaptadores intermedios.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class StrategyEdgeReport:
    """Salud del edge de una estrategia en un instante dado.

    Los opcionales son deliberados: ``None`` significa *no medible con esta
    muestra*, y no debe colapsarse a 0.0 en ningún consumidor. Confundir ambos
    es como se acaba desactivando una estrategia por no tener datos.

    Attributes:
        strategy: Estrategia evaluada.
        sample: Nº de resoluciones usadas (tras aplicar la ventana rodante).
        blocks: Nº de bloques efectivos en que se troceó la muestra.
        expectancy_r: Expectativa rodante, en R.
        profit_factor: Profit factor rodante.
        sharpe: Sharpe por operación (sin anualizar).
        sortino: Sortino por operación (sin anualizar).
        max_drawdown_r: Máximo drawdown rodante, en R.
        edge_decay: R perdida por bloque (positivo = el edge se encoge).
        half_life_trades: Operaciones estimadas hasta la mitad del edge.
        stability_score: Consistencia entre bloques, 0-1.
        edge_persistence: Fracción de bloques con expectativa positiva.
        confidence_drift: Deriva de la confianza declarada, por bloque.
        health_score: Resumen 0-100 de todo lo anterior.
        status: ``healthy`` / ``watch`` / ``degrading`` / ``insufficient_data``.
        reasons: Por qué salió ese estado (auditable, nunca vacío).
    """

    strategy: str
    sample: int
    blocks: int
    expectancy_r: float | None
    profit_factor: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown_r: float
    edge_decay: float | None
    half_life_trades: float | None
    stability_score: float | None
    edge_persistence: float | None
    confidence_drift: float | None
    health_score: float | None
    status: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "sample": self.sample,
            "blocks": self.blocks,
            "expectancy_r": _round(self.expectancy_r),
            "profit_factor": _round(self.profit_factor),
            "sharpe": _round(self.sharpe),
            "sortino": _round(self.sortino),
            "max_drawdown_r": round(self.max_drawdown_r, 4),
            "edge_decay": _round(self.edge_decay),
            "half_life_trades": _round(self.half_life_trades, 1),
            "stability_score": _round(self.stability_score),
            "edge_persistence": _round(self.edge_persistence),
            "confidence_drift": _round(self.confidence_drift),
            "health_score": _round(self.health_score, 1),
            "status": self.status,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyEdgeReport":
        """Rebuild a report from one persisted row.

        Args:
            data: Fila ya parseada del histórico.

        Returns:
            El informe reconstruido.

        Raises:
            KeyError: Si falta ``strategy``.
        """
        return cls(
            strategy=str(data["strategy"]),
            sample=int(data.get("sample", 0)),
            blocks=int(data.get("blocks", 0)),
            expectancy_r=_optional_float(data.get("expectancy_r")),
            profit_factor=_optional_float(data.get("profit_factor")),
            sharpe=_optional_float(data.get("sharpe")),
            sortino=_optional_float(data.get("sortino")),
            max_drawdown_r=float(data.get("max_drawdown_r") or 0.0),
            edge_decay=_optional_float(data.get("edge_decay")),
            half_life_trades=_optional_float(data.get("half_life_trades")),
            stability_score=_optional_float(data.get("stability_score")),
            edge_persistence=_optional_float(data.get("edge_persistence")),
            confidence_drift=_optional_float(data.get("confidence_drift")),
            health_score=_optional_float(data.get("health_score")),
            status=str(data.get("status", "insufficient_data")),
            reasons=tuple(str(r) for r in data.get("reasons", ())),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class EdgeResearchReport:
    """Informe completo de un ciclo: una entrada por estrategia evaluada."""

    generated_at: datetime = field(default_factory=utc_now)
    strategies: tuple[StrategyEdgeReport, ...] = ()
    source: str = "virtual_outcomes"

    @property
    def degrading(self) -> tuple[StrategyEdgeReport, ...]:
        """Estrategias cuyo edge se está deteriorando."""
        return tuple(r for r in self.strategies if r.status == "degrading")

    def by_strategy(self) -> dict[str, StrategyEdgeReport]:
        """Índice por nombre de estrategia."""
        return {report.strategy: report for report in self.strategies}

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "source": self.source,
            "strategies": [report.to_dict() for report in self.strategies],
            "degrading": [report.strategy for report in self.degrading],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EdgeResearchReport":
        """Rebuild a full report from one persisted row.

        Args:
            data: Fila ya parseada del histórico.

        Returns:
            El informe reconstruido.

        Raises:
            KeyError: Si falta ``generated_at``.
            ValueError: Si la marca temporal no es interpretable.
        """
        return cls(
            generated_at=datetime.fromisoformat(str(data["generated_at"])),
            source=str(data.get("source", "virtual_outcomes")),
            strategies=tuple(
                StrategyEdgeReport.from_dict(row) for row in data.get("strategies", ())
            ),
        )


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round without turning ``None`` into a number."""
    return None if value is None else round(value, digits)


def _optional_float(value: Any) -> float | None:
    """Read an optional float back, keeping ``None`` distinct from 0.0."""
    return None if value is None else float(value)

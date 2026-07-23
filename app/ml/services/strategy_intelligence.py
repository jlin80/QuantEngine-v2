"""Inteligencia de estrategias: puntúa cada estrategia con evidencia (Fase 7).

Para cada estrategia calcula su rendimiento reciente, histórico y por activo,
sesión y régimen. Con eso produce un score comparable (0-100) que el Meta
Strategy Manager usa para ajustar dinámicamente los pesos. No opera: sólo mide.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.execution.models.enums import PositionSide
from app.execution.models.trades import TradeRecord
from app.ml.models.math import clamp
from app.ml.services.stats import TradeStats, group_stats

LabeledTrade = tuple[str, TradeRecord]
"""Operación etiquetada con la estrategia que la originó."""


@dataclass(frozen=True, slots=True)
class StrategyScore:
    """Evidence-based score for a single strategy."""

    name: str
    score: float
    historical: TradeStats
    recent: TradeStats
    by_asset: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_session: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_regime: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def trades(self) -> int:
        """Total historical trades for the strategy."""
        return self.historical.trades

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "score": round(self.score, 2),
            "trades": self.trades,
            "historical": self.historical.to_dict(),
            "recent": self.recent.to_dict(),
            "by_asset": self.by_asset,
            "by_session": self.by_session,
            "by_regime": self.by_regime,
        }


class StrategyIntelligence:
    """Rank strategies by recent, historical and segmented performance.

    Args:
        lookback: Nº de operaciones recientes que definen el rendimiento actual.
    """

    def __init__(self, *, lookback: int = 60) -> None:
        self._lookback = lookback

    def rank(self, labeled_trades: Sequence[LabeledTrade]) -> list[StrategyScore]:
        """Compute and sort strategy scores (best first)."""
        by_strategy: dict[str, list[TradeRecord]] = {}
        for name, trade in labeled_trades:
            by_strategy.setdefault(name, []).append(trade)
        scores = [self._score_strategy(name, trades) for name, trades in by_strategy.items()]
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    def _score_strategy(self, name: str, trades: Sequence[TradeRecord]) -> StrategyScore:
        """Build the score for one strategy from its trades."""
        ordered = sorted(trades, key=lambda t: t.exit_time)
        historical = TradeStats.from_trades(ordered)
        recent = TradeStats.from_trades(ordered[-self._lookback :])
        return StrategyScore(
            name=name,
            score=_composite_score(historical, recent),
            historical=historical,
            recent=recent,
            by_asset=group_stats(ordered, "symbol"),
            by_session=group_stats(ordered, "session"),
            by_regime=group_stats(ordered, "regime"),
        )

    @staticmethod
    def label_trades(
        trades: Sequence[TradeRecord], *, key: str = "strategy", default: str = "portfolio"
    ) -> list[LabeledTrade]:
        """Attach a strategy label to each trade from its context snapshot.

        El ``TradeRecord`` no fija una estrategia (la decisión es de consenso);
        se usa ``context_snapshot[key]`` si está presente, con un valor por
        defecto en caso contrario. Cuando la ejecución rellene el snapshot con la
        estrategia dominante, esto queda cableado sin cambios.
        """
        labeled: list[LabeledTrade] = []
        for trade in trades:
            name = str(trade.context_snapshot.get(key, default))
            labeled.append((name, trade))
        return labeled


def _composite_score(historical: TradeStats, recent: TradeStats) -> float:
    """Blend recent and historical evidence into a 0-100 score."""
    if historical.trades == 0:
        return 50.0
    expectancy = 0.6 * recent.expectancy_r + 0.4 * historical.expectancy_r
    profit_factor = 0.6 * recent.profit_factor + 0.4 * historical.profit_factor
    raw = (
        50.0
        + 30.0 * clamp(expectancy, -1.0, 1.0)
        + 12.0 * clamp(profit_factor - 1.0, -1.0, 1.5)
        + 16.0 * (historical.win_rate - 0.5)
    )
    return clamp(raw, 0.0, 100.0)


def side_of(trade: TradeRecord) -> str:
    """Direction label for a trade (helper for the advisor)."""
    return "long" if trade.side is PositionSide.LONG else "short"

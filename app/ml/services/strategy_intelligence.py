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


@dataclass(frozen=True, kw_only=True, slots=True)
class VirtualStrategyStats:
    """Rendimiento de una estrategia según el evaluador continuo (Fase 4).

    Son operaciones **virtuales**: cada señal con niveles se resuelve contra las
    velas posteriores con TP/SL/timeout puros, sin pasar por la ejecución. Miden
    la calidad de la señal *en sí*, aislada de sizing, trailing y salidas por
    régimen.

    Se declara aquí, y no se importa de ``app.engine.evaluation``, para que la
    capa de ML no dependa del motor de estrategias: el composition root adapta
    el ``PerformanceTracker`` a esta forma.
    """

    strategy: str
    evaluated: int
    win_rate: float
    profit_factor: float
    expectancy_r: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "evaluated": self.evaluated,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "expectancy_r": round(self.expectancy_r, 4),
        }

    def score(self) -> float:
        """0-100 score comparable with the executed-trade composite score."""
        raw = (
            50.0
            + 30.0 * clamp(self.expectancy_r, -1.0, 1.0)
            + 12.0 * clamp(self.profit_factor - 1.0, -1.0, 1.5)
            + 16.0 * (self.win_rate - 0.5)
        )
        return clamp(raw, 0.0, 100.0)


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
        """Attach a strategy label to each trade.

        Orden de preferencia:

        1. ``trade.strategy`` — la atribución de primera clase que la ejecución
           rellena desde la decisión de origen (estrategia dominante del
           consenso). Es la fuente correcta desde que existe.
        2. ``context_snapshot[key]`` — compatibilidad con el journal escrito
           antes de que existiera el campo.
        3. ``default`` (``"portfolio"``) — sólo para operaciones que no se
           pueden atribuir a ninguna estrategia (p. ej. posiciones adoptadas del
           broker al arrancar).

        Mientras (1) y (2) no existían, **todas** las operaciones caían en (3):
        el Meta Strategy Manager veía una única entrada agregada ``portfolio``
        en vez de una por estrategia, y por tanto no gobernaba nada útil.
        """
        labeled: list[LabeledTrade] = []
        for trade in trades:
            name = trade.strategy.strip() or str(trade.context_snapshot.get(key, "")).strip()
            labeled.append((name or default, trade))
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

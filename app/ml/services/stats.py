"""Estadística de operaciones reutilizable por la IA (Fase 7).

Calcula, sin dependencias, las métricas que la inteligencia de estrategias, el
asesor de riesgo y el asesor general necesitan: win rate, profit factor,
expectativa (R), tasa de stop, duración media... Todo a partir de las
operaciones cerradas del propio motor.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.execution.models.enums import ExitReason
from app.execution.models.trades import TradeRecord


def session_label(hour: int) -> str:
    """Single market-session label for a UTC hour (for grouping)."""
    if 13 <= hour < 21:
        return "america"
    if 7 <= hour < 13:
        return "europe"
    if hour >= 21 or hour < 7:
        return "asia"
    return "off"


@dataclass(frozen=True, slots=True)
class TradeStats:
    """Aggregate statistics over a set of closed trades."""

    trades: int
    wins: int
    win_rate: float
    profit_factor: float
    expectancy_r: float
    avg_pnl: float
    total_pnl: float
    stop_rate: float
    avg_duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (rounded)."""
        return {
            "trades": self.trades,
            "wins": self.wins,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "expectancy_r": round(self.expectancy_r, 4),
            "avg_pnl": round(self.avg_pnl, 4),
            "total_pnl": round(self.total_pnl, 4),
            "stop_rate": round(self.stop_rate, 4),
            "avg_duration_seconds": round(self.avg_duration_seconds, 1),
        }

    @classmethod
    def from_trades(cls, trades: Sequence[TradeRecord]) -> "TradeStats":
        """Compute the statistics bundle from closed trades."""
        n = len(trades)
        if n == 0:
            return cls(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        wins = sum(1 for t in trades if t.is_win)
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
        total_pnl = sum(t.pnl for t in trades)
        stops = sum(1 for t in trades if t.exit_reason is ExitReason.STOP_LOSS)
        duration = sum(t.duration_seconds for t in trades)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float(gross_profit > 0)
        return cls(
            trades=n,
            wins=wins,
            win_rate=wins / n,
            profit_factor=profit_factor,
            expectancy_r=sum(t.r_multiple for t in trades) / n,
            avg_pnl=total_pnl / n,
            total_pnl=total_pnl,
            stop_rate=stops / n,
            avg_duration_seconds=duration / n,
        )


def group_stats(trades: Sequence[TradeRecord], key: str) -> dict[str, dict[str, Any]]:
    """Compute :class:`TradeStats` per group of trades.

    Args:
        trades: Operaciones cerradas.
        key: Criterio de agrupación: ``symbol`` | ``session`` | ``regime`` |
            ``hour``.

    Returns:
        Mapa grupo → estadística (JSON-safe).
    """
    buckets: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        bucket = _bucket_of(trade, key)
        buckets.setdefault(bucket, []).append(trade)
    return {name: TradeStats.from_trades(items).to_dict() for name, items in buckets.items()}


def _bucket_of(trade: TradeRecord, key: str) -> str:
    """Resolve the grouping bucket for a trade."""
    if key == "symbol":
        return trade.symbol
    if key == "regime":
        return trade.regime
    if key == "hour":
        return f"{trade.entry_time.hour:02d}"
    if key == "session":
        return session_label(trade.entry_time.hour)
    return "all"

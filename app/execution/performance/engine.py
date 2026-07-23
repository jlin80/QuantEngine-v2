"""Performance Engine: calcula automáticamente las métricas de trading.

A partir de las operaciones cerradas del Trade Journal produce un informe con
win rate, profit factor, expectativa, R:R, drawdown máximo, Sharpe, Sortino,
Calmar, Ulcer Index, recovery factor, tiempo medio en mercado y actividad por
día/sesión. Todas las métricas se derivan de una única fuente: los trades.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.execution.models import TradeRecord

# Sesiones por hora UTC (mismas franjas que el Market Context; pueden solaparse).
_SESSION_HOURS: dict[str, tuple[int, int]] = {
    "asia": (0, 9),
    "europe": (7, 16),
    "america": (13, 22),
}


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Informe de desempeño derivado de las operaciones cerradas."""

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    expectancy_r: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    risk_reward: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    ulcer_index: float = 0.0
    recovery_factor: float = 0.0
    average_holding_seconds: float = 0.0
    trades_per_day: float = 0.0
    trades_by_session: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "loss_rate": self.loss_rate,
            "net_profit": self.net_profit,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "expectancy_r": self.expectancy_r,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "risk_reward": self.risk_reward,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "ulcer_index": self.ulcer_index,
            "recovery_factor": self.recovery_factor,
            "average_holding_seconds": self.average_holding_seconds,
            "trades_per_day": self.trades_per_day,
            "trades_by_session": self.trades_by_session,
        }


class PerformanceEngine:
    """Compute performance metrics from closed trades.

    Args:
        initial_balance: Balance inicial (base de las métricas porcentuales).
    """

    def __init__(self, initial_balance: float) -> None:
        self._initial_balance = max(1e-9, initial_balance)

    def compute(self, trades: Sequence[TradeRecord]) -> PerformanceReport:
        """Build the performance report for a set of trades.

        Args:
            trades: Operaciones cerradas (orden cronológico de cierre).

        Returns:
            Informe completo (ceros si no hay operaciones).
        """
        if not trades:
            return PerformanceReport()
        ordered = sorted(trades, key=lambda t: t.exit_time)
        pnls = [t.pnl for t in ordered]
        wins = [t for t in ordered if t.pnl > 0]
        losses = [t for t in ordered if t.pnl < 0]
        total = len(ordered)
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        net_profit = sum(pnls)
        average_win = gross_profit / len(wins) if wins else 0.0
        average_loss = gross_loss / len(losses) if losses else 0.0
        max_dd, max_dd_pct, ulcer = self._drawdown(pnls)
        sharpe, sortino = self._risk_adjusted(pnls)
        return PerformanceReport(
            total_trades=total,
            wins=len(wins),
            losses=len(losses),
            win_rate=round(len(wins) / total, 4),
            loss_rate=round(len(losses) / total, 4),
            net_profit=round(net_profit, 6),
            gross_profit=round(gross_profit, 6),
            gross_loss=round(gross_loss, 6),
            profit_factor=round(self._safe_ratio(gross_profit, gross_loss), 4),
            expectancy=round(net_profit / total, 6),
            expectancy_r=round(sum(t.r_multiple for t in ordered) / total, 4),
            average_win=round(average_win, 6),
            average_loss=round(average_loss, 6),
            risk_reward=round(self._safe_ratio(average_win, average_loss), 4),
            largest_win=round(max((t.pnl for t in ordered), default=0.0), 6),
            largest_loss=round(min((t.pnl for t in ordered), default=0.0), 6),
            max_drawdown=round(max_dd, 6),
            max_drawdown_pct=round(max_dd_pct, 4),
            sharpe=round(sharpe, 4),
            sortino=round(sortino, 4),
            calmar=round(self._calmar(net_profit, max_dd_pct), 4),
            ulcer_index=round(ulcer, 4),
            recovery_factor=round(self._safe_ratio(net_profit, max_dd), 4),
            average_holding_seconds=round(sum(t.duration_seconds for t in ordered) / total, 2),
            trades_per_day=round(self._trades_per_day(ordered), 4),
            trades_by_session=self._trades_by_session(ordered),
        )

    def _drawdown(self, pnls: Sequence[float]) -> tuple[float, float, float]:
        """Return (max drawdown abs, max drawdown %, ulcer index)."""
        equity = self._initial_balance
        peak = equity
        max_dd = 0.0
        max_dd_pct = 0.0
        squared_dd = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            drawdown = peak - equity
            dd_pct = (drawdown / peak * 100.0) if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)
            max_dd_pct = max(max_dd_pct, dd_pct)
            squared_dd += dd_pct**2
        ulcer = math.sqrt(squared_dd / len(pnls)) if pnls else 0.0
        return max_dd, max_dd_pct, ulcer

    def _risk_adjusted(self, pnls: Sequence[float]) -> tuple[float, float]:
        """Per-trade Sharpe and Sortino on returns over the initial balance."""
        if len(pnls) < 2:
            return 0.0, 0.0
        returns = [pnl / self._initial_balance for pnl in pnls]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance)
        downside = [r for r in returns if r < 0]
        downside_var = sum(r**2 for r in downside) / len(returns) if downside else 0.0
        downside_std = math.sqrt(downside_var)
        sharpe = (mean / std * math.sqrt(len(returns))) if std > 0 else 0.0
        sortino = (mean / downside_std * math.sqrt(len(returns))) if downside_std > 0 else 0.0
        return sharpe, sortino

    def _calmar(self, net_profit: float, max_dd_pct: float) -> float:
        """Total return % over max drawdown % (0 if no drawdown)."""
        if max_dd_pct <= 0:
            return 0.0
        total_return_pct = net_profit / self._initial_balance * 100.0
        return total_return_pct / max_dd_pct

    @staticmethod
    def _safe_ratio(numerator: float, denominator: float) -> float:
        """Ratio guarding against division by zero (0 → 0.0)."""
        if denominator <= 0:
            return 0.0
        return numerator / denominator

    @staticmethod
    def _trades_per_day(trades: Sequence[TradeRecord]) -> float:
        """Average number of trades per calendar day of activity."""
        span = (trades[-1].exit_time - trades[0].entry_time).total_seconds()
        days = max(span / 86_400.0, 1e-9)
        return len(trades) / days if span > 0 else float(len(trades))

    @staticmethod
    def _trades_by_session(trades: Sequence[TradeRecord]) -> dict[str, int]:
        """Count trades by the UTC session of their entry (sessions overlap)."""
        counts = dict.fromkeys(_SESSION_HOURS, 0)
        counts["off"] = 0
        for trade in trades:
            hour = trade.entry_time.hour
            matched = False
            for name, (start, end) in _SESSION_HOURS.items():
                if start <= hour < end:
                    counts[name] += 1
                    matched = True
            if not matched:
                counts["off"] += 1
        return counts

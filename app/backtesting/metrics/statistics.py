"""Estadística cuantitativa del laboratorio (Fase 6).

Extiende el :class:`~app.execution.performance.PerformanceReport` del motor de
ejecución (win rate, PF, expectativa, Sharpe, Sortino, Calmar, Ulcer,
recovery, drawdown...) con las métricas propias de validación de firmas
cuantitativas: SQN, MAR, Kelly, payoff, rachas máximas, drawdown medio,
exposición/tiempo en mercado y la estructura para alpha/beta. Reutiliza el
motor de performance existente — no reimplementa lo que ya existe.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.execution.models import TradeRecord
from app.execution.performance import PerformanceEngine, PerformanceReport

if TYPE_CHECKING:
    from app.backtesting.models import EquityPoint


@dataclass(frozen=True, slots=True)
class QuantStatistics:
    """Full statistical fingerprint of a backtest.

    Combina las métricas base del Performance Engine con las de validación
    cuantitativa. Se serializa como un único diccionario plano.
    """

    base: PerformanceReport
    sqn: float
    mar_ratio: float
    annualized_return_pct: float
    kelly_fraction: float
    payoff_ratio: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    average_drawdown_pct: float
    exposure_pct: float
    time_in_market_pct: float
    alpha: float
    beta: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict merging base and extended metrics."""
        payload = self.base.to_dict()
        payload.update(
            {
                "sqn": round(self.sqn, 4),
                "mar_ratio": round(self.mar_ratio, 4),
                "annualized_return_pct": round(self.annualized_return_pct, 4),
                "kelly_fraction": round(self.kelly_fraction, 4),
                "payoff_ratio": round(self.payoff_ratio, 4),
                "max_consecutive_wins": self.max_consecutive_wins,
                "max_consecutive_losses": self.max_consecutive_losses,
                "average_drawdown_pct": round(self.average_drawdown_pct, 4),
                "exposure_pct": round(self.exposure_pct, 4),
                "time_in_market_pct": round(self.time_in_market_pct, 4),
                "alpha": round(self.alpha, 6),
                "beta": round(self.beta, 6),
            }
        )
        return payload


class StatisticsEngine:
    """Compute the extended quant statistics of a backtest.

    Args:
        initial_balance: Balance inicial (base de las métricas porcentuales).
        risk_free_rate: Tasa libre de riesgo anual para el alpha (0 por defecto).
    """

    def __init__(self, initial_balance: float, *, risk_free_rate: float = 0.0) -> None:
        self._initial_balance = max(1e-9, initial_balance)
        self._risk_free_rate = risk_free_rate
        self._performance = PerformanceEngine(initial_balance)

    def compute(
        self,
        trades: Sequence[TradeRecord],
        equity_curve: "Sequence[EquityPoint] | None" = None,
    ) -> QuantStatistics:
        """Compute the full statistics for a set of trades.

        Args:
            trades: Operaciones cerradas.
            equity_curve: Curva de equity muestreada (para exposición y MAR).

        Returns:
            Estadística cuantitativa completa (ceros si no hay operaciones).
        """
        base = self._performance.compute(trades)
        if not trades:
            return QuantStatistics(
                base=base,
                sqn=0.0,
                mar_ratio=0.0,
                annualized_return_pct=0.0,
                kelly_fraction=0.0,
                payoff_ratio=0.0,
                max_consecutive_wins=0,
                max_consecutive_losses=0,
                average_drawdown_pct=0.0,
                exposure_pct=0.0,
                time_in_market_pct=0.0,
                alpha=0.0,
                beta=0.0,
            )
        ordered = sorted(trades, key=lambda t: t.exit_time)
        wins, losses = self._streaks(ordered)
        annualized = self._annualized_return_pct(base.net_profit, equity_curve)
        exposure = self._exposure_pct(equity_curve)
        return QuantStatistics(
            base=base,
            sqn=self._sqn(ordered),
            mar_ratio=self._safe_div(annualized, base.max_drawdown_pct),
            annualized_return_pct=annualized,
            kelly_fraction=self._kelly(base),
            payoff_ratio=base.risk_reward,
            max_consecutive_wins=wins,
            max_consecutive_losses=losses,
            average_drawdown_pct=self._average_drawdown_pct(equity_curve),
            exposure_pct=exposure,
            time_in_market_pct=exposure,
            alpha=self._alpha(annualized, equity_curve),
            beta=0.0,  # estructura preparada: requiere retornos del benchmark
        )

    # ------------------------------------------------------------------
    # Métricas extendidas
    # ------------------------------------------------------------------

    @staticmethod
    def _sqn(trades: Sequence[TradeRecord]) -> float:
        """System Quality Number (Van Tharp) sobre los múltiplos de R."""
        rs = [t.r_multiple for t in trades]
        n = len(rs)
        if n < 2:
            return 0.0
        mean = math.fsum(rs) / n
        variance = math.fsum((r - mean) ** 2 for r in rs) / (n - 1)
        std = math.sqrt(variance)
        if std <= 0:
            return 0.0
        return mean / std * math.sqrt(n)

    @staticmethod
    def _kelly(base: PerformanceReport) -> float:
        """Kelly fraction estimada: p - (1-p)/payoff."""
        payoff = base.risk_reward
        if payoff <= 0:
            return 0.0
        win = base.win_rate
        return win - (1.0 - win) / payoff

    @staticmethod
    def _streaks(trades: Sequence[TradeRecord]) -> tuple[int, int]:
        """Return (max consecutive wins, max consecutive losses)."""
        best_win = best_loss = cur_win = cur_loss = 0
        for trade in trades:
            if trade.pnl > 0:
                cur_win += 1
                cur_loss = 0
            elif trade.pnl < 0:
                cur_loss += 1
                cur_win = 0
            else:
                cur_win = cur_loss = 0
            best_win = max(best_win, cur_win)
            best_loss = max(best_loss, cur_loss)
        return best_win, best_loss

    def _annualized_return_pct(
        self, net_profit: float, equity_curve: "Sequence[EquityPoint] | None"
    ) -> float:
        """CAGR en porcentaje si hay curva con span temporal; si no, retorno total."""
        total_return_pct = net_profit / self._initial_balance * 100.0
        if not equity_curve or len(equity_curve) < 2:
            return total_return_pct
        span = (equity_curve[-1].timestamp - equity_curve[0].timestamp).total_seconds()
        years = span / (365.25 * 86_400.0)
        final = equity_curve[-1].equity
        if years <= 0 or final <= 0 or self._initial_balance <= 0:
            return total_return_pct
        growth = final / self._initial_balance
        return (float(growth ** (1.0 / years)) - 1.0) * 100.0

    @staticmethod
    def _exposure_pct(equity_curve: "Sequence[EquityPoint] | None") -> float:
        """Fracción de velas con al menos una posición abierta, en porcentaje."""
        if not equity_curve:
            return 0.0
        active = sum(1 for point in equity_curve if point.open_positions > 0)
        return active / len(equity_curve) * 100.0

    @staticmethod
    def _average_drawdown_pct(equity_curve: "Sequence[EquityPoint] | None") -> float:
        """Media de los drawdowns positivos a lo largo de la curva."""
        if not equity_curve:
            return 0.0
        drawdowns = [p.drawdown_pct for p in equity_curve if p.drawdown_pct > 0]
        if not drawdowns:
            return 0.0
        return math.fsum(drawdowns) / len(drawdowns)

    def _alpha(
        self, annualized_return_pct: float, equity_curve: "Sequence[EquityPoint] | None"
    ) -> float:
        """Exceso de retorno anualizado sobre la tasa libre de riesgo.

        Beta (frente a un benchmark) queda como estructura preparada; el alpha
        aquí es el exceso simple sobre el risk-free, útil hasta enganchar un
        benchmark de mercado.
        """
        return annualized_return_pct - self._risk_free_rate * 100.0

    @staticmethod
    def _safe_div(numerator: float, denominator: float) -> float:
        """Division guarding against a non-positive denominator."""
        if denominator <= 0:
            return 0.0
        return numerator / denominator

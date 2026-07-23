"""Instantánea del estado de la cartera (JSON-safe, inmutable)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, kw_only=True, slots=True)
class PortfolioSnapshot:
    """Fotografía completa del portafolio en un instante.

    Attributes:
        timestamp: Momento de la instantánea (UTC).
        initial_balance: Balance inicial.
        balance: Balance realizado (caja).
        equity: Balance + PnL flotante.
        floating_pnl: PnL no realizado de las posiciones abiertas.
        realized_pnl: PnL realizado acumulado.
        used_capital: Capital comprometido en posiciones (margen).
        free_capital: Capital libre (equity - usado).
        exposure: Exposición nominal total.
        exposure_pct: Exposición como % del equity.
        peak_equity: Máximo equity alcanzado.
        drawdown_pct: Caída desde el pico en %.
        open_positions: Número de posiciones abiertas.
        total_trades: Operaciones cerradas.
        base_currency: Divisa base.
    """

    timestamp: datetime = field(default_factory=utc_now)
    initial_balance: float
    balance: float
    equity: float
    floating_pnl: float
    realized_pnl: float
    used_capital: float
    free_capital: float
    exposure: float
    exposure_pct: float
    peak_equity: float
    drawdown_pct: float
    open_positions: int
    total_trades: int
    base_currency: str = "USD"

    @property
    def return_pct(self) -> float:
        """Rendimiento total sobre el balance inicial (%)."""
        if self.initial_balance <= 0:
            return 0.0
        return (self.equity - self.initial_balance) / self.initial_balance * 100.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "initial_balance": self.initial_balance,
            "balance": self.balance,
            "equity": self.equity,
            "floating_pnl": self.floating_pnl,
            "realized_pnl": self.realized_pnl,
            "used_capital": self.used_capital,
            "free_capital": self.free_capital,
            "exposure": self.exposure,
            "exposure_pct": self.exposure_pct,
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.drawdown_pct,
            "open_positions": self.open_positions,
            "total_trades": self.total_trades,
            "return_pct": self.return_pct,
            "base_currency": self.base_currency,
        }

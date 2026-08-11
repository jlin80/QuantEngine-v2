"""Risk Manager: la última barrera antes de ejecutar.

Aplica límites duros (riesgo por operación, pérdidas diaria/semanal/mensual,
pérdidas consecutivas, nº de posiciones, exposición total/por símbolo/por
correlación), filtros de mercado (spread, liquidez) y cortacircuitos
(kill switch por drawdown, circuit breaker por pérdida rápida). Ninguna orden
se envía si el Risk Manager no la aprueba.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config.settings import ExecutionRiskSettings
from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class RiskQuery:
    """Datos de una posible entrada que evalúa el Risk Manager.

    Attributes:
        symbol: Símbolo a operar.
        new_notional: Nocional de la orden propuesta.
        equity: Equity actual de la cartera.
        open_positions: Nº de posiciones abiertas.
        positions_on_symbol: Nº de posiciones abiertas en el símbolo.
        symbol_exposures: Exposición nominal actual por símbolo (sin la nueva).
    """

    symbol: str
    new_notional: float
    equity: float
    open_positions: int
    positions_on_symbol: int
    symbol_exposures: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskCheck:
    """Resultado de una evaluación de riesgo."""

    allowed: bool
    rule: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict."""
        return {"allowed": self.allowed, "rule": self.rule, "reason": self.reason}


class RiskManager:
    """Enforce risk limits, market filters and circuit breakers.

    Args:
        settings: Límites y umbrales de riesgo.
        initial_balance: Balance inicial (base de los límites porcentuales).
    """

    def __init__(self, settings: ExecutionRiskSettings, initial_balance: float) -> None:
        self._settings = settings
        self._initial_balance = initial_balance
        self._ledger: list[tuple[datetime, float]] = []
        self._consecutive_losses = 0
        self._kill_switch = False
        self._kill_reason = ""
        self._circuit_breaker = False
        self._circuit_reason = ""
        self._circuit_until: datetime | None = None
        self._last_drawdown_pct = 0.0
        self._log = logging.getLogger("app.execution.risk")

    # ------------------------------------------------------------------
    # Estado de los cortacircuitos
    # ------------------------------------------------------------------

    @property
    def kill_switch_active(self) -> bool:
        """Whether the kill switch is engaged."""
        return self._kill_switch

    @property
    def circuit_breaker_active(self) -> bool:
        """Whether the circuit breaker is currently tripped."""
        self._refresh_circuit_breaker()
        return self._circuit_breaker

    @property
    def consecutive_losses(self) -> int:
        """Current streak of consecutive losing trades."""
        return self._consecutive_losses

    def engage_kill_switch(self, reason: str) -> None:
        """Engage the kill switch (blocks all new entries)."""
        if not self._kill_switch:
            self._log.warning("Kill switch engaged: %s", reason)
        self._kill_switch = True
        self._kill_reason = reason

    def reset_kill_switch(self) -> None:
        """Manually release the kill switch."""
        self._kill_switch = False
        self._kill_reason = ""

    def reset_circuit_breaker(self) -> None:
        """Manually release the circuit breaker."""
        self._circuit_breaker = False
        self._circuit_reason = ""
        self._circuit_until = None

    def emergency_stop(self) -> None:
        """Emergency stop: engage the kill switch immediately."""
        self.engage_kill_switch("emergency_stop")

    def restore(self, *, consecutive_losses: int) -> None:
        """Re-adopt risk state recovered after a restart (Fase 9).

        La racha de pérdidas es un límite duro (``max_consecutive_losses``);
        olvidarla al reiniciar convertiría el reinicio en una forma de saltarse
        el límite.

        Args:
            consecutive_losses: Racha de pérdidas consecutivas previa.
        """
        self._consecutive_losses = max(0, consecutive_losses)

    # ------------------------------------------------------------------
    # Filtros de mercado
    # ------------------------------------------------------------------

    def check_spread(self, spread_bps: float | None) -> RiskCheck:
        """Reject entries when the spread is too wide."""
        if spread_bps is not None and spread_bps > self._settings.max_spread_bps:
            return RiskCheck(
                False,
                "spread_filter",
                f"spread {spread_bps:.1f} bps > máximo {self._settings.max_spread_bps:.1f}",
            )
        return RiskCheck(True)

    def check_liquidity(self, volume: float | None) -> RiskCheck:
        """Reject entries when liquidity is below the minimum."""
        min_liquidity = self._settings.min_liquidity
        if volume is not None and min_liquidity > 0 and volume < min_liquidity:
            return RiskCheck(
                False,
                "liquidity_filter",
                f"liquidez {volume:.2f} < mínimo {min_liquidity:.2f}",
            )
        return RiskCheck(True)

    # ------------------------------------------------------------------
    # Evaluación de entrada
    # ------------------------------------------------------------------

    def evaluate_entry(self, query: RiskQuery) -> RiskCheck:
        """Approve or reject a proposed entry against every hard limit.

        Args:
            query: Datos de la entrada propuesta y estado de la cartera.

        Returns:
            La primera regla incumplida, o ``RiskCheck(allowed=True)``.
        """
        s = self._settings
        if self._kill_switch:
            return RiskCheck(False, "kill_switch", self._kill_reason or "kill switch activo")
        if self.circuit_breaker_active:
            return RiskCheck(False, "circuit_breaker", self._circuit_reason or "circuit breaker")
        if query.new_notional <= 0:
            return RiskCheck(False, "invalid_quantity", "nocional no positivo")
        if self._consecutive_losses >= s.max_consecutive_losses > 0:
            return RiskCheck(
                False,
                "max_consecutive_losses",
                f"{self._consecutive_losses} pérdidas seguidas ≥ {s.max_consecutive_losses}",
            )
        if query.open_positions >= s.max_open_positions:
            return RiskCheck(
                False,
                "max_open_positions",
                f"{query.open_positions} posiciones ≥ máximo {s.max_open_positions}",
            )
        max_positions = s.max_positions_per_symbol_for(query.symbol)
        if query.positions_on_symbol >= max_positions:
            return RiskCheck(
                False,
                "max_positions_per_symbol",
                f"{query.positions_on_symbol} en {query.symbol} ≥ {max_positions}",
            )
        period = self._period_check()
        if period is not None:
            return period
        return self._exposure_check(query)

    def _exposure_check(self, query: RiskQuery) -> RiskCheck:
        """Total / per-symbol / correlated exposure limits."""
        s = self._settings
        equity = query.equity
        if equity <= 0:
            return RiskCheck(False, "insufficient_capital", "equity no positivo")
        # Los mensajes comparan importe contra importe: mezclar "1090" (dólares)
        # con "400%" en la misma frase se lee como si la exposición fuera 1090%.
        current_total = sum(query.symbol_exposures.values())
        projected_total = current_total + query.new_notional
        limit_total = equity * s.max_exposure_pct / 100.0
        if projected_total > limit_total:
            return RiskCheck(
                False,
                "max_exposure",
                f"exposición total {projected_total:.2f} > límite {limit_total:.2f} "
                f"({s.max_exposure_pct:.0f}% de un equity de {equity:.2f})",
            )
        symbol = query.symbol.upper()
        symbol_pct = s.max_symbol_exposure_pct_for(symbol)
        projected_symbol = query.symbol_exposures.get(symbol, 0.0) + query.new_notional
        limit_symbol = equity * symbol_pct / 100.0
        if projected_symbol > limit_symbol:
            return RiskCheck(
                False,
                "max_symbol_exposure",
                f"exposición en {symbol} {projected_symbol:.2f} > límite {limit_symbol:.2f} "
                f"({symbol_pct:.0f}% de un equity de {equity:.2f})",
            )
        correlation_pct = s.max_correlation_exposure_pct_for(symbol)
        correlated = self._correlated_exposure(symbol, query) + query.new_notional
        limit_corr = equity * correlation_pct / 100.0
        if correlated > limit_corr:
            return RiskCheck(
                False,
                "max_correlation_exposure",
                f"exposición correlacionada {correlated:.2f} > límite {limit_corr:.2f} "
                f"({correlation_pct:.0f}% de un equity de {equity:.2f})",
            )
        return RiskCheck(True)

    def _correlated_exposure(self, symbol: str, query: RiskQuery) -> float:
        """Sum current exposure across every correlation group of ``symbol``."""
        total = 0.0
        for group in self._settings.correlation_groups:
            members = {m.upper() for m in group}
            if symbol in members:
                total += sum(
                    exp for sym, exp in query.symbol_exposures.items() if sym.upper() in members
                )
        return total

    def _period_check(self) -> RiskCheck | None:
        """Daily / weekly / monthly realized-loss limits."""
        s = self._settings
        now = utc_now()
        checks = (
            ("max_daily_loss", self._realized_since(_start_of_day(now)), s.max_daily_loss_pct),
            ("max_weekly_loss", self._realized_since(_start_of_week(now)), s.max_weekly_loss_pct),
            (
                "max_monthly_loss",
                self._realized_since(_start_of_month(now)),
                s.max_monthly_loss_pct,
            ),
        )
        for rule, realized, limit_pct in checks:
            limit = -abs(self._initial_balance * limit_pct / 100.0)
            if realized <= limit:
                return RiskCheck(
                    False,
                    rule,
                    f"pérdida {realized:.2f} ≤ límite {limit:.2f} ({limit_pct:.0f}%)",
                )
        return None

    # ------------------------------------------------------------------
    # Actualización de estado
    # ------------------------------------------------------------------

    def on_trade_closed(self, net_pnl: float, *, moment: datetime | None = None) -> None:
        """Register a closed trade and update streak / circuit breaker.

        Args:
            net_pnl: PnL neto de la operación (tras comisiones).
            moment: Momento del cierre (por defecto ahora).
        """
        when = moment or utc_now()
        self._ledger.append((when, net_pnl))
        if net_pnl < 0:
            self._consecutive_losses += 1
        elif net_pnl > 0:
            self._consecutive_losses = 0
        self._check_circuit_breaker(when)

    def update_equity(self, drawdown_pct: float) -> None:
        """Update the running drawdown and trip the kill switch if breached."""
        self._last_drawdown_pct = drawdown_pct
        if drawdown_pct >= self._settings.kill_switch_drawdown_pct > 0:
            self.engage_kill_switch(
                f"drawdown {drawdown_pct:.1f}% ≥ {self._settings.kill_switch_drawdown_pct:.1f}%"
            )

    def _check_circuit_breaker(self, now: datetime) -> None:
        """Trip the circuit breaker when the window loss exceeds the limit."""
        window = timedelta(minutes=self._settings.circuit_breaker_window_minutes)
        realized = sum(pnl for ts, pnl in self._ledger if now - ts <= window)
        limit = -abs(self._initial_balance * self._settings.circuit_breaker_loss_pct / 100.0)
        if realized <= limit:
            self._circuit_breaker = True
            self._circuit_until = now + window
            self._circuit_reason = (
                f"pérdida {realized:.2f} en {self._settings.circuit_breaker_window_minutes:.0f} min"
            )
            self._log.warning("Circuit breaker tripped: %s", self._circuit_reason)

    def _refresh_circuit_breaker(self) -> None:
        """Auto-release the circuit breaker once its window elapses."""
        if (
            self._circuit_breaker
            and self._circuit_until is not None
            and utc_now() >= self._circuit_until
        ):
            self.reset_circuit_breaker()

    def _realized_since(self, since: datetime) -> float:
        """Sum of realized PnL recorded at or after ``since``."""
        return sum(pnl for ts, pnl in self._ledger if ts >= since)

    def status(self) -> dict[str, object]:
        """Full risk status for diagnostics/dashboard."""
        return {
            "kill_switch": self._kill_switch,
            "kill_reason": self._kill_reason,
            "circuit_breaker": self.circuit_breaker_active,
            "circuit_reason": self._circuit_reason,
            "consecutive_losses": self._consecutive_losses,
            "drawdown_pct": round(self._last_drawdown_pct, 4),
            "realized_today": round(self._realized_since(_start_of_day(utc_now())), 4),
            "limits": {
                "max_risk_per_trade_pct": self._settings.max_risk_per_trade_pct,
                "max_daily_loss_pct": self._settings.max_daily_loss_pct,
                "max_open_positions": self._settings.max_open_positions,
                "max_exposure_pct": self._settings.max_exposure_pct,
                "kill_switch_drawdown_pct": self._settings.kill_switch_drawdown_pct,
            },
        }


def _start_of_day(moment: datetime) -> datetime:
    """UTC midnight of the given moment."""
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(moment: datetime) -> datetime:
    """UTC start of the ISO week (Monday 00:00)."""
    return _start_of_day(moment) - timedelta(days=moment.weekday())


def _start_of_month(moment: datetime) -> datetime:
    """UTC start of the calendar month."""
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

"""Seguimiento de madurez en paper trading (Fase 10)."""

from dataclasses import dataclass, field
from datetime import datetime

from app.config.settings import PaperValidationSettings
from app.research.models import PaperTrialStatus
from app.utils.time import utc_now


@dataclass(kw_only=True, slots=True)
class _PaperTrial:
    """Mutable accumulation of a candidate's paper period."""

    genome_id: str
    started_at: datetime
    trades: int = 0
    profit_factor: float = 0.0
    drawdown_pct: float = 0.0
    forced_elapsed_days: float | None = field(default=None)


class PaperValidationTracker:
    """Maturity gate over a candidate's paper-trading period.

    Args:
        settings: Umbrales de madurez (días/operaciones/PF/drawdown).
    """

    def __init__(self, settings: PaperValidationSettings) -> None:
        self._settings = settings
        self._trials: dict[str, _PaperTrial] = {}

    def open(self, genome_id: str, *, started_at: datetime | None = None) -> None:
        """Start (or restart) a paper trial for a candidate."""
        self._trials[genome_id] = _PaperTrial(
            genome_id=genome_id, started_at=started_at or utc_now()
        )

    def update(
        self, genome_id: str, *, trades: int, profit_factor: float, drawdown_pct: float
    ) -> None:
        """Update the accumulated paper statistics for a candidate.

        Raises:
            KeyError: Si el trial no fue abierto.
        """
        trial = self._trials[genome_id]
        trial.trades = trades
        trial.profit_factor = profit_factor
        trial.drawdown_pct = drawdown_pct

    def update_from_stats(self, genome_id: str, statistics: dict[str, float]) -> None:
        """Update from a statistics dict (paper run or backtest proxy)."""
        self.update(
            genome_id,
            trades=int(statistics.get("total_trades", 0)),
            profit_factor=float(statistics.get("profit_factor", 0.0)),
            drawdown_pct=float(statistics.get("max_drawdown_pct", 0.0)),
        )

    def set_elapsed(self, genome_id: str, days: float) -> None:
        """Force the elapsed-days used by the maturity gate (backtest proxy)."""
        self._trials[genome_id].forced_elapsed_days = days

    def status(self, genome_id: str, *, now: datetime | None = None) -> PaperTrialStatus:
        """Evaluate whether the candidate's paper period has matured.

        Args:
            genome_id: Candidata a evaluar.
            now: Instante de referencia (por defecto el reloj del sistema).

        Returns:
            El estado de madurez con los motivos de cualquier gate no cumplido.

        Raises:
            KeyError: Si el trial no fue abierto.
        """
        trial = self._trials[genome_id]
        if trial.forced_elapsed_days is not None:
            days = trial.forced_elapsed_days
        else:
            reference = now or utc_now()
            days = max(0.0, (reference - trial.started_at).total_seconds() / 86400.0)

        reasons: list[str] = []
        if days < self._settings.min_days:
            reasons.append(f"período {days:.1f}d < {self._settings.min_days:.1f}d")
        if trial.trades < self._settings.min_trades:
            reasons.append(f"operaciones {trial.trades} < {self._settings.min_trades}")
        if trial.profit_factor < self._settings.min_profit_factor:
            reasons.append(
                f"profit factor {trial.profit_factor:.2f} < {self._settings.min_profit_factor:.2f}"
            )
        if trial.drawdown_pct > self._settings.max_drawdown_pct:
            reasons.append(
                f"drawdown {trial.drawdown_pct:.1f}% > {self._settings.max_drawdown_pct:.1f}%"
            )
        matured = not reasons
        if matured:
            reasons.append("madura: cumple el período y los mínimos de evidencia")
        return PaperTrialStatus(
            genome_id=genome_id,
            matured=matured,
            days=days,
            trades=trial.trades,
            profit_factor=trial.profit_factor,
            drawdown_pct=trial.drawdown_pct,
            reasons=tuple(reasons),
        )

    def active(self) -> list[str]:
        """Genome ids with an open paper trial."""
        return list(self._trials)

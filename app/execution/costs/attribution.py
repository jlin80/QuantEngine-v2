"""Cost Attribution Engine (Bloque 9) — en qué se va el bruto.

Separa el PnL bruto de todo lo que se lo come: comisiones, slippage, spread,
latencia, coste oculto y coste de oportunidad. La pregunta que responde no es
"¿cuánto gané?" sino "¿cuánto dejé por el camino, y en qué tramo?".

**Tres honestidades estructurales de este módulo.**

1. **El coste oculto es un residuo, y por eso se llama así.** Se calcula como
   ``bruto - neto - (comisiones + slippage + spread)``. Si sale grande,
   significa que hay un coste que el sistema **no está midiendo**, no que exista
   un concepto llamado "oculto". Es un detector de contabilidad incompleta, y
   verlo crecer es la señal de que falta instrumentar algo.
2. **La latencia no se atribuye si no se registró.** El Trade Journal no guarda
   la latencia de cada operación, así que su coste sale como no observable en
   vez de estimarse. Estimarlo lo mezclaría con el residuo y perderíamos justo
   la señal del punto anterior.
3. **El coste de oportunidad se mide con señales que nunca se ejecutaron**, no
   con conjeturas: el evaluador continuo (Fase 4) ya resuelve cada señal contra
   el mercado posterior. Una señal con R virtual positiva que no llegó a
   operación es una oportunidad perdida **medida**, no imaginada.
"""

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.config.settings import CostAttributionSettings
from app.engine.evaluation.outcomes import VirtualOutcome
from app.execution.models.trades import TradeRecord
from app.utils.time import utc_now

TradesProvider = Callable[[], Sequence[TradeRecord]]
OutcomesProvider = Callable[[], Sequence[VirtualOutcome]]


@dataclass(frozen=True, kw_only=True, slots=True)
class CostBreakdown:
    """Reparto del bruto entre neto y costes, en dinero de la cuenta.

    Attributes:
        trades: Operaciones incluidas.
        gross_pnl: PnL antes de cualquier coste registrado.
        net_pnl: PnL después de todo lo que el sistema descuenta.
        commission: Comisiones.
        slippage: Coste del slippage medido, en dinero.
        spread: Coste del spread medido, en dinero.
        latency: Coste de la latencia. ``None`` = **no registrado**, no cero.
        hidden: Residuo sin explicar. Grande = falta instrumentar algo.
        opportunity: Coste de oportunidad medido con señales no ejecutadas.
            ``None`` si no hay resultados virtuales con los que medirlo.
    """

    trades: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    spread: float = 0.0
    latency: float | None = None
    hidden: float = 0.0
    opportunity: float | None = None

    @property
    def total_measured(self) -> float:
        """Costes efectivamente medidos (sin residuo ni oportunidad)."""
        return self.commission + self.slippage + self.spread

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "trades": self.trades,
            "gross_pnl": round(self.gross_pnl, 4),
            "net_pnl": round(self.net_pnl, 4),
            "commission": round(self.commission, 4),
            "slippage": round(self.slippage, 4),
            "spread": round(self.spread, 4),
            "latency": None if self.latency is None else round(self.latency, 4),
            "hidden": round(self.hidden, 4),
            "opportunity": None if self.opportunity is None else round(self.opportunity, 4),
            "total_measured": round(self.total_measured, 4),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class CostReport:
    """Informe de costes: total, por día y sus advertencias."""

    generated_at: datetime = field(default_factory=utc_now)
    total: CostBreakdown = field(default_factory=CostBreakdown)
    daily: dict[str, CostBreakdown] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "total": self.total.to_dict(),
            "daily": {day: breakdown.to_dict() for day, breakdown in sorted(self.daily.items())},
            # Las notas son parte del informe, no un adorno: dicen qué costes no
            # se están midiendo y cuánto de grande es el agujero.
            "notes": list(self.notes),
        }


class CostAttributionEngine:
    """Split gross PnL into everything that eats it.

    Args:
        settings: Umbrales y ventana.
        trades_provider: Fuente de operaciones cerradas (Trade Journal).
        outcomes_provider: Fuente de resultados virtuales por señal (Fase 4),
            para poder medir el coste de oportunidad. Opcional: sin ella ese
            coste queda como no medido, que es distinto de cero.
    """

    def __init__(
        self,
        settings: CostAttributionSettings,
        trades_provider: TradesProvider,
        outcomes_provider: OutcomesProvider | None = None,
    ) -> None:
        self._settings = settings
        self._trades = trades_provider
        self._outcomes = outcomes_provider
        self._last: CostReport | None = None
        self._log = logging.getLogger("app.execution.costs")

    def analyze(self) -> CostReport:
        """Build the full cost report, total and per day.

        Returns:
            El informe, con una nota por cada coste que no se está midiendo y
            otra si el residuo sin explicar supera el umbral configurado.
        """
        trades = list(self._trades())[-self._settings.max_trades :]
        total = self._breakdown(trades)
        daily: dict[str, CostBreakdown] = {}
        by_day: dict[date, list[TradeRecord]] = defaultdict(list)
        for trade in trades:
            by_day[trade.exit_time.date()].append(trade)
        for day, items in by_day.items():
            daily[day.isoformat()] = self._breakdown(items, with_opportunity=False)

        notes: list[str] = []
        if total.latency is None:
            notes.append(
                "coste de latencia no medido: el Trade Journal no registra la latencia "
                "por operación, así que cae dentro del residuo"
            )
        if total.opportunity is None:
            notes.append("coste de oportunidad no medido: sin resultados virtuales por señal")
        if total.gross_pnl != 0.0:
            ratio = abs(total.hidden) / max(abs(total.gross_pnl), 1e-9)
            if ratio > self._settings.hidden_alert_ratio:
                notes.append(
                    f"residuo sin explicar = {ratio:.1%} del bruto: hay costes que el "
                    "sistema no está midiendo"
                )
        report = CostReport(generated_at=utc_now(), total=total, daily=daily, notes=tuple(notes))
        self._last = report
        return report

    def _breakdown(
        self, trades: Sequence[TradeRecord], *, with_opportunity: bool = True
    ) -> CostBreakdown:
        """Split one set of trades into its cost components."""
        if not trades:
            return CostBreakdown(opportunity=self._opportunity_cost() if with_opportunity else None)
        gross = sum(t.pnl_gross for t in trades)
        net = sum(t.pnl for t in trades)
        commission = sum(t.commission for t in trades)
        slippage = sum(_bps_to_money(t, t.slippage_bps) for t in trades)
        spread = sum(_bps_to_money(t, t.spread_bps) for t in trades)
        # El residuo es lo que queda tras descontar lo que sí se mide. Puede ser
        # negativo (el bruto ya venía con algo descontado) y eso también informa.
        hidden = (gross - net) - (commission + slippage + spread)
        return CostBreakdown(
            trades=len(trades),
            gross_pnl=gross,
            net_pnl=net,
            commission=commission,
            slippage=slippage,
            spread=spread,
            latency=None,
            hidden=hidden,
            opportunity=self._opportunity_cost() if with_opportunity else None,
        )

    def _opportunity_cost(self) -> float | None:
        """Measure what the signals that never traded would have made.

        Sólo cuentan las que **habrían ganado**: una señal no ejecutada que
        habría perdido no es un coste de oportunidad, es una bala esquivada, y
        sumarla con signo contrario compensaría las dos cosas hasta dejar el
        número en nada.

        Returns:
            El coste en R, o ``None`` sin fuente de resultados virtuales.
        """
        if self._outcomes is None:
            return None
        outcomes = list(self._outcomes())
        if not outcomes:
            return None
        traded = {sid for trade in self._trades() for sid in trade.signal_ids}
        missed = [o for o in outcomes if o.signal_id not in traded and o.r_multiple > 0]
        return sum(o.r_multiple for o in missed)

    def last_report(self) -> CostReport | None:
        """Informe del último análisis."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "last_report": None if self._last is None else self._last.to_dict(),
        }


def _bps_to_money(trade: TradeRecord, bps: float) -> float:
    """Convert a bps cost into account money for one trade.

    Sobre el nocional de **entrada** y en unidades del subyacente, no en lotes:
    en oro un lote son 100 onzas, y medir el coste sobre `quantity` lo dejaría
    100 veces por debajo — el mismo error que costó el incidente del
    `contract_size` (ADR-099).
    """
    notional = trade.entry_price * trade.quantity * trade.contract_size
    return notional * bps / 10_000.0

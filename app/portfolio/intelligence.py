"""Portfolio Intelligence (Bloque 8) — de dónde sale el dinero, de verdad.

Responde una pregunta que el PnL agregado esconde: si el resultado del mes es
+3%, ¿viene de las cuatro estrategias por igual, o de una racha de un símbolo en
una sesión concreta que no se va a repetir? La respuesta cambia por completo qué
hacer a continuación, y el número agregado no la contiene.

**Dos advertencias que gobiernan la lectura de este módulo.**

1. **Esto mide PnL realizado, no exposición.** La concentración que se calcula
   aquí dice de dónde *salió* el dinero, no dónde está el riesgo *ahora*. Un
   portfolio perfectamente repartido puede tener toda su exposición viva en un
   solo símbolo. Son preguntas distintas y este módulo sólo responde la primera.
2. **Una contribución alta no es una virtud.** Puede ser una racha. Por eso cada
   contribución viaja con su número de operaciones y su expectativa en R: sin
   eso, "el 80% del PnL vino de `vol_breakout`" se lee como mérito cuando puede
   ser una muestra de nueve operaciones.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import PortfolioIntelligenceSettings
from app.execution.models.trades import TradeRecord
from app.utils.time import utc_now

# Las cuatro dimensiones por las que el bloque pide desglosar la contribución.
DIMENSIONS: tuple[str, ...] = ("symbol", "strategy", "session", "regime")


@dataclass(frozen=True, kw_only=True, slots=True)
class Contribution:
    """Aporte de un grupo (un símbolo, una estrategia, una sesión…).

    Attributes:
        key: Nombre del grupo.
        trades: Operaciones del grupo.
        pnl: PnL neto aportado.
        pnl_share: Fracción del PnL **positivo** total. Se usa el positivo como
            denominador a propósito: con ganancias y pérdidas mezcladas, la suma
            neta puede acercarse a cero y las cuotas se disparan a cifras
            absurdas o cambian de signo. Sobre el positivo, "aportó el 40% de lo
            que se ganó" siempre significa lo mismo.
        gross_pnl: PnL bruto (antes de comisiones).
        commission: Comisiones pagadas por el grupo.
        expectancy_r: R media por operación.
        win_rate: Fracción de ganadoras.
    """

    key: str
    trades: int
    pnl: float
    pnl_share: float
    gross_pnl: float
    commission: float
    expectancy_r: float | None
    win_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "key": self.key,
            "trades": self.trades,
            "pnl": round(self.pnl, 4),
            "pnl_share": round(self.pnl_share, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "commission": round(self.commission, 4),
            "expectancy_r": None if self.expectancy_r is None else round(self.expectancy_r, 4),
            "win_rate": None if self.win_rate is None else round(self.win_rate, 4),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class PortfolioReport:
    """Radiografía del portfolio sobre las operaciones cerradas.

    Attributes:
        generated_at: Momento del cálculo.
        trades: Operaciones analizadas.
        net_pnl: PnL neto total.
        gross_pnl: PnL bruto total.
        commission: Comisiones totales.
        contributions: Contribución por dimensión, ordenada de mayor a menor.
        heatmap: PnL por (símbolo, estrategia). Es donde se ve si una estrategia
            gana en todos los activos o vive de uno solo.
        concentration: Índice de Herfindahl sobre las cuotas de PnL, 0-1.
        effective_bets: ``1/concentration``. Cuántas fuentes de PnL
            *independientes* habría que tener para producir esta concentración.
            Es la lectura legible: "el equivalente a 1.4 apuestas" dice más que
            "HHI 0.71".
        sample_warning: Aviso cuando la muestra no sostiene las conclusiones.
    """

    generated_at: datetime = field(default_factory=utc_now)
    trades: int = 0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    commission: float = 0.0
    contributions: dict[str, tuple[Contribution, ...]] = field(default_factory=dict)
    heatmap: dict[str, dict[str, float]] = field(default_factory=dict)
    concentration: float | None = None
    effective_bets: float | None = None
    sample_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "trades": self.trades,
            "net_pnl": round(self.net_pnl, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "commission": round(self.commission, 4),
            "contributions": {
                dimension: [c.to_dict() for c in items]
                for dimension, items in self.contributions.items()
            },
            "heatmap": self.heatmap,
            "concentration": None if self.concentration is None else round(self.concentration, 4),
            "effective_bets": (
                None if self.effective_bets is None else round(self.effective_bets, 2)
            ),
            "sample_warning": self.sample_warning,
            # El aviso viaja en la carga útil, como en el Bloque 2: esto se lee
            # en un dashboard, lejos de la documentación que lo matiza.
            "caveat": "PnL realizado, no exposición viva; una contribución alta puede ser racha",
        }


class PortfolioIntelligence:
    """Break down realized PnL by symbol, strategy, session and regime.

    Args:
        settings: Mínimos de muestra y límites.
        trades_provider: Fuente de operaciones cerradas (Trade Journal).
    """

    def __init__(
        self,
        settings: PortfolioIntelligenceSettings,
        trades_provider: Any,
    ) -> None:
        self._settings = settings
        self._trades = trades_provider
        self._last: PortfolioReport | None = None
        self._log = logging.getLogger("app.portfolio.intelligence")

    def analyze(self) -> PortfolioReport:
        """Build the full report from the closed trades available.

        Returns:
            El informe. Con muestra por debajo del mínimo se calcula igual pero
            se rellena ``sample_warning``: los números existen, y esconderlos
            no ayuda; lo que hace falta es que nadie los tome por concluyentes.
        """
        trades = list(self._trades())[-self._settings.max_trades :]
        if not trades:
            report = PortfolioReport(sample_warning="sin operaciones cerradas")
            self._last = report
            return report

        net = sum(t.pnl for t in trades)
        gross = sum(t.pnl_gross for t in trades)
        commission = sum(t.commission for t in trades)
        contributions = {
            dimension: self._contributions(trades, dimension) for dimension in DIMENSIONS
        }
        symbol_shares = [c.pnl_share for c in contributions["symbol"]]
        concentration = _herfindahl(symbol_shares)
        report = PortfolioReport(
            generated_at=utc_now(),
            trades=len(trades),
            net_pnl=net,
            gross_pnl=gross,
            commission=commission,
            contributions=contributions,
            heatmap=self._heatmap(trades),
            concentration=concentration,
            effective_bets=None if not concentration else 1.0 / concentration,
            sample_warning=(
                ""
                if len(trades) >= self._settings.min_sample
                else f"muestra {len(trades)} < mínimo {self._settings.min_sample}: "
                "los desgloses no son concluyentes"
            ),
        )
        self._last = report
        return report

    def _contributions(
        self, trades: Sequence[TradeRecord], dimension: str
    ) -> tuple[Contribution, ...]:
        """Group trades by one dimension and measure each group."""
        groups: dict[str, list[TradeRecord]] = {}
        for trade in trades:
            groups.setdefault(_key_of(trade, dimension), []).append(trade)
        total_positive = sum(t.pnl for t in trades if t.pnl > 0)
        out: list[Contribution] = []
        for key, items in groups.items():
            pnl = sum(t.pnl for t in items)
            r_values = [t.r_multiple for t in items]
            out.append(
                Contribution(
                    key=key,
                    trades=len(items),
                    pnl=pnl,
                    pnl_share=(pnl / total_positive) if total_positive > 0 else 0.0,
                    gross_pnl=sum(t.pnl_gross for t in items),
                    commission=sum(t.commission for t in items),
                    expectancy_r=(sum(r_values) / len(r_values)) if r_values else None,
                    win_rate=(sum(1 for t in items if t.pnl > 0) / len(items)) if items else None,
                )
            )
        return tuple(sorted(out, key=lambda c: -c.pnl))

    def _heatmap(self, trades: Sequence[TradeRecord]) -> dict[str, dict[str, float]]:
        """PnL per (symbol, strategy) pair."""
        grid: dict[str, dict[str, float]] = {}
        for trade in trades:
            symbol = trade.symbol or "unknown"
            strategy = trade.strategy or "unattributed"
            row = grid.setdefault(symbol, {})
            row[strategy] = round(row.get(strategy, 0.0) + trade.pnl, 4)
        return grid

    def last_report(self) -> PortfolioReport | None:
        """Informe del último análisis."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "last_report": None if self._last is None else self._last.to_dict(),
        }


def _key_of(trade: TradeRecord, dimension: str) -> str:
    """Group key of a trade for one dimension.

    Las operaciones sin atribución van a un grupo propio (``unattributed``) en
    vez de repartirse o descartarse: son las adoptadas del bróker y las
    anteriores a la trazabilidad, y verlas juntas dice cuánta parte del PnL no
    se puede explicar todavía.
    """
    if dimension == "symbol":
        return trade.symbol or "unknown"
    if dimension == "strategy":
        return trade.strategy or "unattributed"
    if dimension == "regime":
        return trade.regime or "unknown"
    return _session_of(trade.entry_time)


def _session_of(moment: datetime) -> str:
    """Session bucket of a UTC timestamp."""
    hour = moment.hour
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 13:
        return "europe"
    if 13 <= hour < 21:
        return "america"
    return "late"


def _herfindahl(shares: Sequence[float]) -> float | None:
    """Herfindahl index over contribution shares.

    Se calcula sobre las cuotas **positivas** normalizadas: una cuota negativa
    (un grupo que pierde) elevada al cuadrado sumaría concentración, que es
    justo lo contrario de lo que significa. Un grupo que pierde no concentra el
    origen del beneficio: lo diluye.

    Args:
        shares: Cuotas de PnL por grupo.

    Returns:
        Índice 0-1 (1 = todo el beneficio viene de un solo grupo), o ``None`` si
        no hay ninguna cuota positiva de la que hablar.
    """
    positive = [s for s in shares if s > 0]
    total = sum(positive)
    if total <= 0:
        return None
    return sum((s / total) ** 2 for s in positive)

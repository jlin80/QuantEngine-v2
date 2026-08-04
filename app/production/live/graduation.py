"""Criterios estadísticos de graduación a live, y medición del hueco actual.

Desde la Fase 5 existe la regla *"solo se habilita live tras cumplir criterios
estadísticos"*, pero esos criterios **nunca se escribieron en ningún sitio** —
está anotado como riesgo conocido de la Fase 5. Una regla sin umbrales es una
intención, no un control: no se puede incumplir porque no se puede evaluar.

Este módulo los formaliza y —lo que importa más— **mide cuánto falta** contra el
Trade Journal real.

**Cumplirlos no activa nada.** No toca el `LiveGate`, no toca `allow_live` y no
tiene ningún camino hacia `resolved_mode()`. Es un informe. La decisión de
operar en real sigue siendo humana, manual y posterior — el guard anti-live
permanece intacto pase lo que pase con estos números.
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.execution.models.enums import ExitReason
from app.execution.models.trades import TradeRecord

# ----------------------------------------------------------------------
# Umbrales
# ----------------------------------------------------------------------

MIN_TRADES = 400
"""Muestra mínima de operaciones limpias.

Con 100 operaciones y una expectativa de +0.1R, el error estándar tapa el
resultado entero: no se distingue edge de suerte. 400 es el orden de magnitud a
partir del cual una expectativa modesta empieza a ser medible, no una cifra
mágica.
"""

MIN_EXPECTANCY_R = 0.10
"""Expectativa mínima por operación, en múltiplos de R.

Positiva y con margen: exigir >0 aprobaría un sistema que empata, y un sistema
que empata en paper pierde en real (el paper no cobra swaps ni sufre requotes).
"""

MIN_PROFIT_FACTOR = 1.30
"""Profit factor mínimo. Por debajo de ~1.2 el resultado lo domina el ruido."""

MAX_DRAWDOWN_PCT = 15.0
"""Drawdown máximo tolerado sobre el equity pico, en porcentaje."""

MIN_DAYS_IN_PAPER = 60
"""Días mínimos de operativa continuada.

El calendario importa aparte de la muestra: 400 operaciones en tres días miden
un único régimen de mercado con mucho detalle.
"""

MIN_REGIMES = 3
"""Regímenes distintos que deben estar representados (Regime Detection, Fase 3)."""

MIN_TRADES_PER_REGIME = 30
"""Operaciones mínimas para considerar un régimen 'cubierto' y no anecdótico."""

MAX_FORCED_EXIT_PCT = 50.0
"""Tope de salidas decididas por la ejecución, no por la tesis.

Este criterio no estaba en el enunciado y se añade por lo que dicen los datos:
si el motor cierra la mayoría de sus posiciones por régimen, tiempo o kill
switch, **sus estrategias casi nunca llegan a poner a prueba su propia tesis**.
Un sistema así puede tener expectativa positiva y aun así no haber demostrado
nada sobre sus señales — y lo que se graduaría a real es la ejecución, no la
estrategia.
"""

_THESIS_EXITS = frozenset(
    {
        ExitReason.TAKE_PROFIT,
        ExitReason.STOP_LOSS,
        ExitReason.TRAILING_STOP,
        ExitReason.BREAK_EVEN,
    }
)


@dataclass(frozen=True, kw_only=True, slots=True)
class GraduationCriterion:
    """Un criterio evaluado contra los datos reales.

    Attributes:
        name: Identificador.
        description: Qué exige, en una línea.
        target: Umbral requerido (texto, para que se lea igual que se escribió).
        actual: Valor medido.
        passed: Si se cumple.
        gap: Qué falta para cumplirlo (vacío si ya se cumple).
    """

    name: str
    description: str
    target: str
    actual: str
    passed: bool
    gap: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "description": self.description,
            "target": self.target,
            "actual": self.actual,
            "passed": self.passed,
            "gap": self.gap,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class GraduationReport:
    """Estado de los criterios de graduación frente al historial real.

    Attributes:
        criteria: Criterios evaluados, en orden.
        sample: Tamaño de la muestra considerada.
    """

    criteria: tuple[GraduationCriterion, ...] = field(default_factory=tuple)
    sample: int = 0

    @property
    def met(self) -> bool:
        """Whether every criterion is satisfied.

        Aun siendo ``True``, **no habilita live**: sigue siendo una decisión
        humana y explícita, con el guard anti-live intacto.
        """
        return all(c.passed for c in self.criteria)

    @property
    def pending(self) -> tuple[GraduationCriterion, ...]:
        """Criterios que todavía no se cumplen."""
        return tuple(c for c in self.criteria if not c.passed)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "sample": self.sample,
            "met": self.met,
            "pending": len(self.pending),
            "criteria": [c.to_dict() for c in self.criteria],
            "note": (
                "Cumplir estos criterios NO activa live trading. La habilitación "
                "sigue siendo una decisión manual del operador, con el guard "
                "anti-live intacto hasta ese momento."
            ),
        }


def _profit_factor(r_values: list[float]) -> float:
    gross_win = sum(v for v in r_values if v > 0)
    gross_loss = -sum(v for v in r_values if v <= 0)
    if gross_loss <= 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def _max_drawdown_pct(trades: list[TradeRecord], starting_equity: float) -> float:
    """Peak-to-trough drawdown of the realised equity curve, in percent."""
    if starting_equity <= 0:
        return 0.0
    equity = starting_equity
    peak = starting_equity
    worst = 0.0
    for trade in sorted(trades, key=lambda t: t.exit_time):
        equity += trade.pnl
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return worst


def evaluate_graduation(
    trades: list[TradeRecord],
    *,
    starting_equity: float = 500.0,
) -> GraduationReport:
    """Measure the real journal against the graduation criteria.

    Args:
        trades: Operaciones cerradas a considerar (ya filtradas por era si se
            quiere medir sólo el historial limpio).
        starting_equity: Equity de partida para el drawdown porcentual.

    Returns:
        El informe con cada criterio, su valor real y lo que falta.
    """
    n = len(trades)
    if n == 0:
        return GraduationReport(
            criteria=(
                GraduationCriterion(
                    name="sample",
                    description="Muestra de operaciones cerradas",
                    target=f">= {MIN_TRADES}",
                    actual="0",
                    passed=False,
                    gap="No hay operaciones que evaluar.",
                ),
            ),
            sample=0,
        )

    r_values = [t.r_multiple for t in trades]
    expectancy = statistics.fmean(r_values)
    profit_factor = _profit_factor(r_values)
    drawdown = _max_drawdown_pct(trades, starting_equity)
    span = max(t.exit_time for t in trades) - min(t.entry_time for t in trades)
    days = span / timedelta(days=1)

    regimes = Counter(t.regime for t in trades if t.regime and t.regime != "unknown")
    covered = {name: count for name, count in regimes.items() if count >= MIN_TRADES_PER_REGIME}
    forced = sum(1 for t in trades if t.exit_reason not in _THESIS_EXITS)
    forced_pct = forced / n * 100.0

    criteria = (
        GraduationCriterion(
            name="sample",
            description="Muestra de operaciones cerradas post-fixes",
            target=f">= {MIN_TRADES}",
            actual=str(n),
            passed=n >= MIN_TRADES,
            gap="" if n >= MIN_TRADES else f"Faltan {MIN_TRADES - n} operaciones.",
        ),
        GraduationCriterion(
            name="expectancy_r",
            description="Expectativa por operación en múltiplos de R",
            target=f">= {MIN_EXPECTANCY_R:+.2f}R",
            actual=f"{expectancy:+.3f}R",
            passed=expectancy >= MIN_EXPECTANCY_R,
            gap=(
                ""
                if expectancy >= MIN_EXPECTANCY_R
                else f"Faltan {MIN_EXPECTANCY_R - expectancy:.3f}R por operación."
            ),
        ),
        GraduationCriterion(
            name="profit_factor",
            description="Profit factor",
            target=f">= {MIN_PROFIT_FACTOR:.2f}",
            actual=f"{profit_factor:.2f}",
            passed=profit_factor >= MIN_PROFIT_FACTOR,
            gap=(
                ""
                if profit_factor >= MIN_PROFIT_FACTOR
                else f"Faltan {MIN_PROFIT_FACTOR - profit_factor:.2f} puntos."
            ),
        ),
        GraduationCriterion(
            name="max_drawdown",
            description="Drawdown máximo sobre equity pico",
            target=f"<= {MAX_DRAWDOWN_PCT:.0f}%",
            actual=f"{drawdown:.1f}%",
            passed=drawdown <= MAX_DRAWDOWN_PCT,
            gap=(
                ""
                if drawdown <= MAX_DRAWDOWN_PCT
                else f"Excede en {drawdown - MAX_DRAWDOWN_PCT:.1f} puntos."
            ),
        ),
        GraduationCriterion(
            name="days_in_paper",
            description="Días de operativa continuada",
            target=f">= {MIN_DAYS_IN_PAPER}",
            actual=f"{days:.1f}",
            passed=days >= MIN_DAYS_IN_PAPER,
            gap=(
                "" if days >= MIN_DAYS_IN_PAPER else f"Faltan {MIN_DAYS_IN_PAPER - days:.0f} días."
            ),
        ),
        GraduationCriterion(
            name="regime_coverage",
            description=(f"Regímenes distintos con >= {MIN_TRADES_PER_REGIME} operaciones"),
            target=f">= {MIN_REGIMES}",
            actual=f"{len(covered)} ({', '.join(sorted(covered)) or '—'})",
            passed=len(covered) >= MIN_REGIMES,
            gap=(
                ""
                if len(covered) >= MIN_REGIMES
                else f"Faltan {MIN_REGIMES - len(covered)} régimen(es) con muestra suficiente."
            ),
        ),
        GraduationCriterion(
            name="forced_exits",
            description="Salidas decididas por la ejecución, no por la tesis",
            target=f"<= {MAX_FORCED_EXIT_PCT:.0f}%",
            actual=f"{forced_pct:.1f}%",
            passed=forced_pct <= MAX_FORCED_EXIT_PCT,
            gap=(
                ""
                if forced_pct <= MAX_FORCED_EXIT_PCT
                else (
                    f"Excede en {forced_pct - MAX_FORCED_EXIT_PCT:.1f} puntos: las "
                    f"estrategias casi nunca llegan a poner a prueba su tesis."
                )
            ),
        ),
    )

    return GraduationReport(criteria=criteria, sample=n)

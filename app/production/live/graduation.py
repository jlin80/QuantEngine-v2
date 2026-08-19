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

Los umbrales son **constantes de módulo, no configuración**: un límite de
habilitación de live que se afloja con una variable de entorno no es un
control. Cambiarlos exige tocar este fichero, y por tanto aparece en el diff.

*Criterio descartado a propósito:* se evaluó exigir un corte a
``P(expectativa > 0)`` y se dejó fuera por redundante — el bootstrap devuelve
ese mismo p-valor, así que es la misma evidencia que ``expectancy_ci``
expresada de otra forma. Pedir las dos cosas cuenta un solo hecho dos veces y
da falsa sensación de rigor. Ver ``docs/graduation_criteria.md``.
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.backtesting.session_edge import bootstrap_expectancy
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

BOOTSTRAP_RESAMPLES = 10_000
"""Remuestreos del bootstrap de la expectativa. Fijo: dos informes del mismo
journal tienen que dar el mismo número o no es reportable."""

WALK_FORWARD_FOLDS = 3
"""Pliegues cronológicos en los que se parte el journal para el walk-forward."""

MIN_WALK_FORWARD_FOLDS = 2
"""Pliegues que deben confirmar la decisión fuera de muestra.

Exigir los tres sería exigir que el sistema no tenga un solo mal trimestre;
exigir uno no distingue señal de suerte. Dos de tres es el punto donde una
confirmación aislada deja de bastar.
"""

MIN_TRADES_PER_FOLD = 30
"""Operaciones mínimas en un bloque para que su expectativa signifique algo.

Un pliegue con menos no cuenta como confirmado **ni** como fallado: es
inconcluyente, y se trata como no confirmado (fail-closed).
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


def _walk_forward_confirmations(trades: list[TradeRecord]) -> tuple[int, int]:
    """Pliegues cuya decisión in-sample se sostiene fuera de muestra.

    La estabilidad por sub-periodos —que el signo se repita— **no es lo mismo**
    que el walk-forward: el walk-forward pregunta si una decisión tomada con
    datos pasados sobrevive a datos que no vio. Aquí la "decisión" es la única
    que toma esta puerta: *¿el sistema despejaba el listón de expectativa con lo
    que se sabía hasta la frontera?*

    El journal se ordena por salida y se parte en ``WALK_FORWARD_FOLDS + 1``
    bloques contiguos. Para cada frontera, el in-sample es todo lo anterior y el
    out-of-sample es el bloque siguiente, que no se vuelve a tocar.

    Un pliegue **confirma** sólo si se dan las dos cosas: el in-sample despeja
    ``MIN_EXPECTANCY_R`` (o sea, con esos datos se habría promovido) *y* el
    out-of-sample sale positivo. Si el in-sample no despeja, el pliegue no
    confirma nada — no es un fallo del sistema, es que no había nada que
    validar, y contarlo como éxito sería premiar la ausencia de señal. Es
    exactamente lo que ocurrió en agosto de 2026, cuando los dos pliegues
    medidos dieron conjunto de selección vacío.

    Args:
        trades: Operaciones cerradas, en cualquier orden.

    Returns:
        ``(confirmados, evaluados)``. ``evaluados`` puede ser menor que
        ``WALK_FORWARD_FOLDS`` si no hay muestra para tantos bloques.
    """
    ordered = sorted(trades, key=lambda t: t.exit_time)
    blocks = WALK_FORWARD_FOLDS + 1
    if len(ordered) < blocks * MIN_TRADES_PER_FOLD:
        return 0, 0

    size = len(ordered) // blocks
    confirmed = 0
    evaluated = 0
    for fold in range(1, blocks):
        in_sample = ordered[: fold * size]
        out_sample = ordered[fold * size : (fold + 1) * size]
        if len(out_sample) < MIN_TRADES_PER_FOLD:
            continue
        evaluated += 1
        in_expectancy = statistics.fmean(t.r_multiple for t in in_sample)
        if in_expectancy < MIN_EXPECTANCY_R:
            continue  # no se habría promovido: nada que confirmar
        if statistics.fmean(t.r_multiple for t in out_sample) > 0.0:
            confirmed += 1
    return confirmed, evaluated


def evaluate_graduation(
    trades: list[TradeRecord],
    *,
    starting_equity: float = 500.0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> GraduationReport:
    """Measure the real journal against the graduation criteria.

    Args:
        trades: Operaciones cerradas a considerar (ya filtradas por era si se
            quiere medir sólo el historial limpio).
        starting_equity: Equity de partida para el drawdown porcentual.
        resamples: Remuestreos del bootstrap. Sólo afecta a la **precisión** del
            intervalo, nunca a dónde está el umbral, así que bajarlo en tests no
            afloja ningún criterio.

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

    ci_low, _, _ = bootstrap_expectancy(r_values, resamples=resamples)
    wf_confirmed, wf_evaluated = _walk_forward_confirmations(trades)

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
            name="expectancy_ci",
            description="Límite inferior del IC 95 % de la expectativa (bootstrap)",
            target="> 0.000R",
            actual=f"{ci_low:+.3f}R",
            passed=ci_low > 0.0,
            gap=(
                ""
                if ci_low > 0.0
                else (
                    "La expectativa medida no se distingue de cero: con esta muestra, "
                    "un sistema sin ventaja daría este resultado."
                )
            ),
        ),
        GraduationCriterion(
            name="walk_forward",
            description="Pliegues cuya decisión in-sample se sostiene fuera de muestra",
            target=f">= {MIN_WALK_FORWARD_FOLDS} de {WALK_FORWARD_FOLDS}",
            actual=(
                f"{wf_confirmed} de {wf_evaluated} evaluados"
                if wf_evaluated
                else "sin muestra para partir en pliegues"
            ),
            passed=wf_confirmed >= MIN_WALK_FORWARD_FOLDS,
            gap=(
                ""
                if wf_confirmed >= MIN_WALK_FORWARD_FOLDS
                else (
                    "Una decisión que no sobrevive a datos que no vio es un ajuste "
                    "al pasado, no una ventaja."
                )
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

"""Edge por (estrategia, sesión): medición con corrección estadística.

Responde una pregunta concreta —*¿alguna estrategia tiene edge estable en
alguna sesión de este símbolo?*— y está construido para que la respuesta
**pueda ser que no**. El protocolo, los umbrales y el kill criteria están
fijados en ``docs/session_edge.md`` y se escribieron antes de la primera
corrida.

Las tres trampas que este módulo evita a propósito:

1. **Comparaciones múltiples.** Con ~20 estrategias × ~6 sesiones hay ~120
   celdas. A p<0.05 y sin corregir, unas seis parecen ganadoras por puro azar.
   Se aplica Benjamini-Hochberg (FDR) sobre el conjunto de celdas elegibles.
2. **Un solo periodo bueno.** El histórico se parte en sub-periodos contiguos y
   se exige que el signo de la ventaja se repita en todos. Es exactamente lo
   que faltó cuando el ranking BTC/ETH resultó ser ruido (r = +0.084).
3. **Confundir señal con ejecución.** Se cuentan por separado las señales
   resueltas por el evaluador continuo y las operaciones ejecutadas. Una celda
   puede tener señal buena y ejecución mala, y no es lo mismo.

Muestra insuficiente **no se rellena ni se omite**: es una categoría de salida.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

# Sesiones activas simultáneas se unen con "+" para formar la celda: los
# solapes son celdas propias. Europa-América no se comporta como Europa sola,
# y colapsarlo a la primera sesión activa perdería justo esa distinción.
NO_SESSION = "off"


def session_cell(sessions: Sequence[str] | None) -> str:
    """Nombre de celda para un conjunto de sesiones activas.

    Args:
        sessions: Sesiones activas (puede venir vacía o ``None``).

    Returns:
        ``"europe+america"``, ``"asia"``… o ``"off"`` si no había ninguna.
        ``None`` (dato ausente) devuelve ``"desconocida"``: la ausencia de
        medición no es ``off``.
    """
    if sessions is None:
        return "desconocida"
    active = [s for s in sessions if s]
    if not active:
        return NO_SESSION
    return "+".join(sorted(active))


@dataclass(slots=True)
class CellSample:
    """Muestra cruda de una celda (estrategia, sesión) antes de resumirla."""

    strategy: str
    session: str
    trade_r: list[float] = field(default_factory=list)
    trade_times: list[datetime] = field(default_factory=list)
    signal_r: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CellResult:
    """Resultado evaluado de una celda, con su veredicto y su porqué."""

    strategy: str
    session: str
    n_signals: int
    n_trades: int
    expectancy_r: float | None
    signal_expectancy_r: float | None
    profit_factor: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    subperiod_expectancy: tuple[float | None, ...]
    stable_across_subperiods: bool
    survives_fdr: bool
    verdict: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        """JSON-safe representation (``None`` se conserva, nunca pasa a 0.0)."""
        return {
            "strategy": self.strategy,
            "session": self.session,
            "n_signals": self.n_signals,
            "n_trades": self.n_trades,
            "expectancy_r": self.expectancy_r,
            "signal_expectancy_r": self.signal_expectancy_r,
            "profit_factor": self.profit_factor,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "p_value": self.p_value,
            "subperiod_expectancy": list(self.subperiod_expectancy),
            "stable_across_subperiods": self.stable_across_subperiods,
            "survives_fdr": self.survives_fdr,
            "verdict": self.verdict,
            "reason": self.reason,
        }


def bootstrap_expectancy(
    values: Sequence[float], *, resamples: int = 10_000, seed: int = 20260811
) -> tuple[float, float, float]:
    """Bootstrap percentil de la expectancy media.

    Args:
        values: R-múltiplos de la celda.
        resamples: Remuestreos (fijado, no ajustable por corrida, para que dos
            ejecuciones del mismo dato den el mismo número).
        seed: Semilla — el bootstrap es aleatorio, y un resultado que cambia
            entre corridas no es reportable.

    Returns:
        ``(ci_low, ci_high, p_value)`` al 95 %. El p-valor es unilateral:
        proporción de remuestreos cuya media **no** es positiva, es decir, la
        evidencia contra "esta celda gana".

    Raises:
        ValueError: Si la muestra está vacía.
    """
    if not values:
        raise ValueError("bootstrap sobre muestra vacía")
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    non_positive = 0
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        mean = total / n
        means.append(mean)
        if mean <= 0.0:
            non_positive += 1
    means.sort()
    low = means[int(0.025 * (resamples - 1))]
    high = means[int(0.975 * (resamples - 1))]
    return low, high, non_positive / resamples


def benjamini_hochberg(p_values: Sequence[float], *, q: float = 0.10) -> list[bool]:
    """Benjamini-Hochberg: qué hipótesis se rechazan controlando el FDR a ``q``.

    Se usa FDR y no Bonferroni porque Bonferroni sobre ~120 celdas y muestras de
    decenas de operaciones no rechazaría nunca nada: convertiría la corrección
    en un "no" automático, que tampoco es una medición.

    Args:
        p_values: P-valores en el orden de las celdas.
        q: Tasa de falsos descubrimientos admitida.

    Returns:
        Lista de booleanos alineada con ``p_values``.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    cutoff_rank = 0
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= q * rank / m:
            cutoff_rank = rank
    accepted = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= cutoff_rank:
            accepted[idx] = True
    return accepted


def profit_factor(values: Sequence[float]) -> float | None:
    """Profit factor de una serie de R (``None`` si no hay pérdidas que dividir)."""
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return gains / losses


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate_cells(
    samples: Sequence[CellSample],
    *,
    boundaries: Sequence[datetime],
    min_trades: int = 30,
    min_signals: int = 30,
    fdr_q: float = 0.10,
    resamples: int = 10_000,
) -> list[CellResult]:
    """Aplicar el kill criteria completo a todas las celdas medidas.

    Args:
        samples: Muestras crudas por celda.
        boundaries: Fronteras internas de los sub-periodos (len = n_sub - 1).
        min_trades: Umbral de muestra ejecutada.
        min_signals: Umbral de muestra de señal.
        fdr_q: Tasa de FDR.
        resamples: Remuestreos del bootstrap.

    Returns:
        Un :class:`CellResult` por celda, incluidas las de muestra insuficiente
        y las que no muestran edge — la tabla completa es el entregable, no
        sólo las supervivientes.
    """
    eligible: list[CellSample] = []
    stats: dict[tuple[str, str], tuple[float, float, float]] = {}
    for sample in samples:
        if len(sample.trade_r) >= min_trades:
            eligible.append(sample)
            stats[(sample.strategy, sample.session)] = bootstrap_expectancy(
                sample.trade_r, resamples=resamples
            )

    # La corrección se aplica SOLO sobre las celdas elegibles: incluir las de
    # muestra insuficiente inflaría m y haría la corrección más severa por
    # culpa de celdas que ni siquiera se estaban probando.
    passed = benjamini_hochberg([stats[(s.strategy, s.session)][2] for s in eligible], q=fdr_q)
    fdr_by_cell = {(s.strategy, s.session): ok for s, ok in zip(eligible, passed, strict=True)}

    results: list[CellResult] = []
    for sample in samples:
        key = (sample.strategy, sample.session)
        n_trades = len(sample.trade_r)
        n_signals = len(sample.signal_r)
        expectancy = _mean(sample.trade_r)
        sub = _subperiod_expectancy(sample, boundaries)
        stable = all(value is not None and value > 0 for value in sub)

        if n_trades < min_trades or n_signals < min_signals:
            results.append(
                CellResult(
                    strategy=sample.strategy,
                    session=sample.session,
                    n_signals=n_signals,
                    n_trades=n_trades,
                    expectancy_r=expectancy,
                    signal_expectancy_r=_mean(sample.signal_r),
                    profit_factor=profit_factor(sample.trade_r),
                    ci_low=None,
                    ci_high=None,
                    p_value=None,
                    subperiod_expectancy=sub,
                    stable_across_subperiods=stable,
                    survives_fdr=False,
                    verdict="muestra_insuficiente",
                    reason=(
                        f"n_trades={n_trades} (<{min_trades}) / "
                        f"n_señales={n_signals} (<{min_signals})"
                    ),
                )
            )
            continue

        ci_low, ci_high, p_value = stats[key]
        survives = fdr_by_cell.get(key, False)
        if expectancy is not None and expectancy > 0 and ci_low > 0 and survives and stable:
            verdict, reason = "edge_estable", "cumple las cuatro condiciones"
        elif expectancy is not None and expectancy > 0:
            verdict = "inestable"
            missing = []
            if ci_low <= 0:
                missing.append("IC inferior <= 0")
            if not survives:
                missing.append("no sobrevive FDR")
            if not stable:
                missing.append("cambia de signo entre sub-periodos")
            reason = "expectancy > 0 pero " + ", ".join(missing)
        else:
            verdict, reason = "sin_edge", "expectancy <= 0"

        results.append(
            CellResult(
                strategy=sample.strategy,
                session=sample.session,
                n_signals=n_signals,
                n_trades=n_trades,
                expectancy_r=expectancy,
                signal_expectancy_r=_mean(sample.signal_r),
                profit_factor=profit_factor(sample.trade_r),
                ci_low=ci_low,
                ci_high=ci_high,
                p_value=p_value,
                subperiod_expectancy=sub,
                stable_across_subperiods=stable,
                survives_fdr=survives,
                verdict=verdict,
                reason=reason,
            )
        )
    return results


def _subperiod_expectancy(
    sample: CellSample, boundaries: Sequence[datetime]
) -> tuple[float | None, ...]:
    """Expectancy por sub-periodo (``None`` donde no hubo operaciones).

    ``None`` **no** cuenta como estable: un sub-periodo sin operaciones no
    confirma nada, y tratarlo como aprobado sería aceptar la ausencia de
    evidencia como evidencia.
    """
    buckets: list[list[float]] = [[] for _ in range(len(boundaries) + 1)]
    for r_value, moment in zip(sample.trade_r, sample.trade_times, strict=True):
        index = 0
        for boundary in boundaries:
            if moment >= boundary:
                index += 1
        buckets[index].append(r_value)
    return tuple(_mean(bucket) for bucket in buckets)


def verdict_summary(
    results: Sequence[CellResult], *, min_cells: int = 3, min_sessions: int = 2
) -> dict[str, object]:
    """Aplicar el kill criteria global: ¿vale la pena cablear pesos por sesión?

    Args:
        results: Celdas evaluadas.
        min_cells: Mínimo de celdas con edge estable.
        min_sessions: Mínimo de sesiones distintas entre ellas.

    Returns:
        Veredicto global y su justificación explícita.
    """
    stable = [r for r in results if r.verdict == "edge_estable"]
    sessions = {r.session for r in stable}
    counts = {
        verdict: sum(1 for r in results if r.verdict == verdict)
        for verdict in ("edge_estable", "inestable", "sin_edge", "muestra_insuficiente")
    }
    if not stable:
        decision = "no_hay_edge_estable_por_sesion"
        why = "ninguna celda cumple las cuatro condiciones del kill criteria"
    elif len(stable) < min_cells:
        decision = "insuficiente_para_segmentar"
        why = (
            f"{len(stable)} celda(s) con edge estable, por debajo del mínimo de "
            f"{min_cells}: bajo la hipótesis nula con FDR q=0.10 se espera ≈1 falso positivo"
        )
    elif len(sessions) < min_sessions:
        decision = "senal_en_una_franja"
        why = (
            f"las {len(stable)} celdas ganadoras están todas en {sorted(sessions)}: "
            "es una observación, no varias"
        )
    else:
        decision = "procede_proponer_pesos_por_sesion"
        why = f"{len(stable)} celdas con edge estable en {len(sessions)} sesiones distintas"
    return {
        "decision": decision,
        "why": why,
        "counts": counts,
        "stable_cells": [f"{r.strategy}@{r.session}" for r in stable],
    }

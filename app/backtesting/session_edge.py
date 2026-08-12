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
    """Muestra cruda de una celda (estrategia, sesión) antes de resumirla.

    ``trade_holding_s`` y ``trade_exit`` son del Bloque 7 (¿esto sigue siendo
    scalping?): sin el motivo de salida, un holding corto no distingue "llegó
    a su objetivo rápido" de "algo lo cortó antes de tiempo".
    """

    strategy: str
    session: str
    trade_r: list[float] = field(default_factory=list)
    trade_times: list[datetime] = field(default_factory=list)
    trade_holding_s: list[float] = field(default_factory=list)
    trade_exit: list[str] = field(default_factory=list)
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
    holding_median_s: float | None = None
    exit_mix: tuple[tuple[str, float], ...] = ()

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
            "holding_median_s": self.holding_median_s,
            "exit_mix": dict(self.exit_mix),
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
                    holding_median_s=_median(sample.trade_holding_s),
                    exit_mix=_exit_mix(sample.trade_exit),
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
                holding_median_s=_median(sample.trade_holding_s),
                exit_mix=_exit_mix(sample.trade_exit),
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


def _median(values: Sequence[float]) -> float | None:
    """Mediana de la muestra (``None`` si está vacía).

    Mediana y no media: el holding tiene cola larga, y una operación de dos
    horas mueve la media sin describir a las otras mil.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _exit_mix(reasons: Sequence[str]) -> tuple[tuple[str, float], ...]:
    """Reparto de motivos de salida, de mayor a menor."""
    if not reasons:
        return ()
    total = len(reasons)
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return tuple(sorted(((k, v / total) for k, v in counts.items()), key=lambda kv: -kv[1]))


def scalping_check(results: Sequence[CellResult], *, max_holding_s: float) -> dict[str, object]:
    """Bloque 7: ¿alguna celda se sale del régimen de scalping?

    La pregunta del enunciado es si el edge de alguna celda aparece **sólo con
    holdings largos**. Se responde en dos direcciones, porque medir sólo una
    daría por buena la mitad del problema:

    - **por arriba**: celdas cuyo holding mediano supera ``max_holding_s``;
    - **por abajo**: celdas cuya salida dominante no es ni objetivo ni stop, es
      decir, operaciones cortadas antes de que su tesis se resolviera. Un
      holding corto no prueba que el sistema haga scalping: puede probar que
      algo lo interrumpe.

    Args:
        results: Celdas evaluadas (sólo se miran las de muestra suficiente).
        max_holding_s: Techo del régimen de scalping, en segundos.

    Returns:
        Veredicto, celdas fuera de rango y el reparto de salidas dominante.
    """
    measured = [r for r in results if r.verdict != "muestra_insuficiente" and r.holding_median_s]
    too_long = [
        f"{r.strategy}@{r.session}"
        for r in measured
        if r.holding_median_s is not None and r.holding_median_s > max_holding_s
    ]
    # Una celda "resuelve su tesis" cuando sale por su propio objetivo o su
    # propio stop. Cualquier otra cosa (régimen, tiempo, kill switch) la corta.
    resolved = {"take_profit", "stop_loss", "trailing_stop"}
    cut_short = [
        f"{r.strategy}@{r.session}"
        for r in measured
        if r.exit_mix and r.exit_mix[0][0] not in resolved
    ]
    holdings = [r.holding_median_s for r in measured if r.holding_median_s is not None]
    with_edge_and_long = [
        f"{r.strategy}@{r.session}"
        for r in measured
        if r.verdict == "edge_estable"
        and r.holding_median_s is not None
        and r.holding_median_s > max_holding_s
    ]
    if with_edge_and_long:
        decision = "edge_solo_con_holdings_largos"
    elif too_long:
        decision = "holdings_largos_sin_edge"
    else:
        decision = "dentro_del_regimen_de_scalping"
    return {
        "decision": decision,
        "max_holding_s": max_holding_s,
        "cells_measured": len(measured),
        "holding_median_s": _median(holdings),
        "cells_over_threshold": too_long,
        "cells_with_edge_over_threshold": with_edge_and_long,
        "cells_cut_before_thesis": cut_short,
    }


def walk_forward(
    samples: Sequence[CellSample],
    *,
    boundaries: Sequence[datetime],
    min_trades: int = 30,
    fdr_q: float = 0.10,
    resamples: int = 10_000,
) -> list[dict[str, object]]:
    """Bloque 1: walk-forward IS→OOS **de la regla de selección**.

    Un walk-forward valida una decisión tomada con datos pasados. Aquí la
    decisión es la del kill criteria: *«esta celda tiene edge»*. Cada pliegue
    aplica esa regla usando **sólo** las operaciones anteriores a la frontera
    (in-sample) y después mide, sin volver a elegir, qué hicieron esas mismas
    celdas en el bloque siguiente (out-of-sample).

    Es la diferencia que faltó en el ranking BTC/ETH: allí se eligió y se midió
    sobre el mismo tramo, y el ranking resultó ser ruido (r = +0.084).

    Args:
        samples: Muestras crudas por celda, con sus tiempos.
        boundaries: Fronteras internas que definen los bloques (len = n - 1).
        min_trades: Muestra mínima exigida **en cada lado** del pliegue.
        fdr_q: Tasa de FDR aplicada dentro del in-sample.
        resamples: Remuestreos del bootstrap.

    Returns:
        Un registro por pliegue. Una selección vacía es un resultado
        —significa que la regla no eligió nada que validar— y se reporta como
        tal, no como un pliegue omitido.
    """
    blocks: list[datetime | None] = [*boundaries, None]
    folds: list[dict[str, object]] = []
    for index in range(len(blocks) - 1):
        is_end = blocks[index]
        oos_end = blocks[index + 1]
        if is_end is None:
            continue
        selected: list[str] = []
        candidates = 0
        p_values: list[float] = []
        keys: list[tuple[CellSample, float, float]] = []
        for sample in samples:
            in_sample = [
                r for r, t in zip(sample.trade_r, sample.trade_times, strict=True) if t < is_end
            ]
            if len(in_sample) < min_trades:
                continue
            candidates += 1
            expectancy = _mean(in_sample)
            ci_low, _, p_value = bootstrap_expectancy(in_sample, resamples=resamples)
            p_values.append(p_value)
            keys.append((sample, expectancy or 0.0, ci_low))
        passed = benjamini_hochberg(p_values, q=fdr_q)
        oos_values: list[float] = []
        for (sample, expectancy, ci_low), survives in zip(keys, passed, strict=True):
            if not (expectancy > 0 and ci_low > 0 and survives):
                continue
            selected.append(f"{sample.strategy}@{sample.session}")
            oos_values.extend(
                r
                for r, t in zip(sample.trade_r, sample.trade_times, strict=True)
                if t >= is_end and (oos_end is None or t < oos_end)
            )
        folds.append(
            {
                "fold": index + 1,
                "is_end": is_end.isoformat(),
                "oos_end": oos_end.isoformat() if oos_end is not None else None,
                "cells_eligible_in_sample": candidates,
                "cells_selected_in_sample": selected,
                "oos_trades": len(oos_values),
                "oos_expectancy_r": _mean(oos_values),
            }
        )
    return folds


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

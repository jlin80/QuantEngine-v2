"""¿Hay edge estable en algún régimen de mercado? (Gap de investigación nº6).

Segmenta el Trade Journal **real** por el régimen observado al abrir la
posición y aplica el mismo kill criteria que `docs/session_edge.md` fijó para
las sesiones, para no cambiar la vara de medir según el resultado:

1. muestra mínima por celda,
2. intervalo de confianza bootstrap que no toque el cero,
3. corrección de Benjamini-Hochberg **en los dos sentidos** (ganar y perder),
4. estabilidad del signo en sub-periodos.

Se corre sobre el journal, no sobre backtest, a propósito: el laboratorio quedó
reconciliado con producción (2026-08-21) pero sigue sin el canal de cierres
`manual` del broker y construye las velas desde barras nativas en vez de desde
ticks. Para una pregunta de segmentación, el registro real es la fuente directa.

Uso:
    python -m scripts.regime_edge --symbol XAUUSDM --desde 2026-08-11
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
from collections import defaultdict
from pathlib import Path

_MIN_TRADES = 30
_FDR_Q = 0.05
_BOOTSTRAP = 2000
_SUBPERIODS = 3


def _load(path: Path, symbol: str, desde: str, hasta: str) -> list[tuple[str, str, float]]:
    """Read (fecha, régimen, R) for the closed trades in range."""
    rows: list[tuple[str, str, float]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("symbol") != symbol:
                continue
            day = str(record.get("entry_time") or "")[:10]
            if not desde <= day <= hasta:
                continue
            r = record.get("r_multiple")
            if r is None:
                continue
            rows.append((day, str(record.get("regime") or "unknown"), float(r)))
    return rows


def _bootstrap_ci(values: list[float], rng: random.Random) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean."""
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(_BOOTSTRAP))
    return means[int(_BOOTSTRAP * 0.025)], means[int(_BOOTSTRAP * 0.975) - 1]


def _p_value(values: list[float]) -> float:
    """Two-sided p-value of mean != 0 (normal approximation)."""
    n = len(values)
    sd = st.stdev(values)
    if sd == 0.0:
        return 1.0
    z = abs(sum(values) / n) / (sd / n**0.5)
    # Aproximación de la cola normal (Abramowitz-Stegun 26.2.17), suficiente
    # para ordenar p-valores en un FDR; no se reporta como cifra exacta.
    t = 1.0 / (1.0 + 0.2316419 * z)
    poly = t * (
        0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    tail = 0.3989422804014327 * math.exp(-z * z / 2.0) * poly
    return 2.0 * float(tail)


def _benjamini_hochberg(pvalues: dict[str, float], q: float) -> set[str]:
    """Return the cells that survive BH at level ``q``."""
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    survivors: set[str] = set()
    m = len(ordered)
    for rank, (_name, p) in enumerate(ordered, start=1):
        if p <= q * rank / m:
            survivors = {n for n, _ in ordered[:rank]}
    return survivors


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Edge por régimen sobre el journal real")
    parser.add_argument("--symbol", default="XAUUSDM")
    parser.add_argument("--desde", default="2026-08-11", help="Inicio (config actual)")
    parser.add_argument("--hasta", default="2026-12-31")
    parser.add_argument("--journal", default="data/execution/journal.jsonl")
    args = parser.parse_args()

    rows = _load(Path(args.journal), args.symbol, args.desde, args.hasta)
    if not rows:
        print("Sin operaciones en el rango.")
        return

    by_regime: dict[str, list[float]] = defaultdict(list)
    for _, regime, r in rows:
        by_regime[regime].append(r)

    days = sorted({day for day, _, _ in rows})
    chunk = max(1, len(days) // _SUBPERIODS)
    windows = [set(days[i : i + chunk]) for i in range(0, len(days), chunk)][:_SUBPERIODS]

    rng = random.Random(7)
    print(f"{args.symbol}  {args.desde} → {args.hasta}   n={len(rows)}  dias={len(days)}")
    print()
    header = (
        f"{'regimen':<14}{'n':>6}{'expect.':>10}"
        f"{'IC 95% bootstrap':>26}{'p':>9}  {'signo estable':<14}"
    )
    print(header)
    print("-" * len(header))

    elegibles: dict[str, float] = {}
    detalle: dict[str, tuple[int, float, tuple[float, float], bool]] = {}
    for regime, values in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        n = len(values)
        mean = sum(values) / n
        if n < _MIN_TRADES:
            print(f"{regime:<14}{n:>6}{mean:>+10.4f}{'(muestra insuficiente)':>26}")
            continue
        low, high = _bootstrap_ci(values, rng)
        p = _p_value(values)
        signs: list[float] = []
        for window in windows:
            sub = [r for day, reg, r in rows if reg == regime and day in window]
            if sub:
                signs.append(sum(sub) / len(sub))
        estable = bool(signs) and (all(s > 0 for s in signs) or all(s < 0 for s in signs))
        print(
            f"{regime:<14}{n:>6}{mean:>+10.4f}"
            f"{f'[{low:+.4f}, {high:+.4f}]':>26}{p:>9.4f}  {'si' if estable else 'NO':<14}"
        )
        detalle[regime] = (n, mean, (low, high), estable)
        if low > 0 or high < 0:
            elegibles[regime] = p

    print()
    survivors = _benjamini_hochberg(elegibles, _FDR_Q) if elegibles else set()
    print(f"Celdas con IC que no toca el cero: {sorted(elegibles) or 'ninguna'}")
    print(f"Sobreviven a Benjamini-Hochberg (q={_FDR_Q}): {sorted(survivors) or 'ninguna'}")
    aprobadas = [r for r in survivors if detalle[r][3]]
    print(
        f"...y ademas con signo estable en {_SUBPERIODS} sub-periodos: "
        f"{sorted(aprobadas) or 'ninguna'}"
    )
    print()
    if not aprobadas:
        print("VEREDICTO: no hay edge estable por regimen con la muestra disponible.")
    else:
        for regime in sorted(aprobadas):
            n, mean, _, _ = detalle[regime]
            print(f"VEREDICTO: {regime} sobrevive (n={n}, {mean:+.4f}R).")


if __name__ == "__main__":
    main()

"""Segmenta el Trade Journal REAL buscando condiciones observables al entrar.

Por que este analisis y no "que opere solo las buenas": cual operacion fue buena
solo se sabe al cerrarla. Filtrar por el resultado es mirar el futuro. Lo unico
accionable es encontrar **condiciones conocidas en el momento de abrir** que
separen ganadoras de perdedoras.

Se apoya en la maquinaria que ya existe (`bootstrap_expectancy`,
`benjamini_hochberg`): con ~10 dimensiones y varias celdas cada una hay decenas
de comparaciones, y sin correccion por comparaciones multiples unas cuantas
parecen ganadoras por puro azar.

Uso:
    python -m scripts.journal_segments data/execution/journal.jsonl XAUUSDM

Se corre en la VPS, donde vive el journal. Solo lee: no opera, no toca la
configuracion y no habilita nada.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtesting.session_edge import benjamini_hochberg, bootstrap_expectancy

MIN_N = 30
"""Celdas con menos operaciones no se evaluan: no se rellenan ni se omiten,
se reportan como muestra insuficiente."""


def load(path: Path, symbol: str) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("symbol", "")).upper() == symbol.upper():
            rows.append(r)
    return rows


def bucket_num(value: float | None, edges: list[float], label: str) -> str:
    if value is None:
        return f"{label}=?"
    for e in edges:
        if value < e:
            return f"{label}<{e}"
    return f"{label}>={edges[-1]}"


def dimensions(r: dict[str, Any]) -> dict[str, str]:
    """Solo cosas CONOCIDAS AL ABRIR. Nada de exit_reason ni duracion."""
    conf = r.get("confidence")
    score = r.get("score")
    spread = r.get("spread_bps")
    return {
        "strategy": str(r.get("strategy") or "?"),
        "categoria": str(r.get("strategy_category") or "?"),
        "regimen": str(r.get("regime") or "?"),
        "volatilidad": str(r.get("volatility") or "?"),
        "lado": str(r.get("side") or "?"),
        "confianza": bucket_num(conf, [0.6, 0.75, 0.85], "conf"),
        "score": bucket_num(score, [60, 70, 80], "score"),
        "spread": bucket_num(spread, [0.5, 1.0, 2.0], "spr"),
    }


def main() -> int:
    path = Path(sys.argv[1])
    symbol = sys.argv[2] if len(sys.argv) > 2 else "XAUUSDM"
    rows = load(path, symbol)
    rs = [float(r.get("r_multiple", 0) or 0) for r in rows]
    print(f"{symbol}: {len(rows)} operaciones | expectativa global {sum(rs)/len(rs):+.4f}R")

    lo, hi, p = bootstrap_expectancy(rs, resamples=5000)
    print(f"  IC 95% global: [{lo:+.4f}, {hi:+.4f}]   p={p:.4f}")
    print("  (si el IC contiene 0, el sistema no se distingue de no tener ventaja)\n")

    # --- por que se pierde: diagnostico ex-post -------------------------
    print("=" * 74)
    print("POR QUE SE PIERDE  (motivo de salida; ex-post, diagnostico)")
    print("=" * 74)
    ex: dict[str, list[float]] = collections.defaultdict(list)
    for r in rows:
        ex[str(r.get("exit_reason") or "?")].append(float(r.get("r_multiple", 0) or 0))
    print(f"{'motivo':<18}{'ops':>7}{'% ops':>8}{'exp R':>10}{'aporte R total':>16}")
    print("-" * 60)
    for k, v in sorted(ex.items(), key=lambda kv: -sum(kv[1])):
        print(
            f"{k:<18}{len(v):>7}{len(v)/len(rows)*100:>7.1f}%"
            f"{sum(v)/len(v):>10.4f}{sum(v):>16.1f}"
        )

    # --- celdas observables al entrar -----------------------------------
    cells: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for r in rows:
        rv = float(r.get("r_multiple", 0) or 0)
        for dim, val in dimensions(r).items():
            cells[(dim, val)].append(rv)

    evaluables = {k: v for k, v in cells.items() if len(v) >= MIN_N}
    small = len(cells) - len(evaluables)

    stats: dict[tuple[str, str], tuple[int, float, float, float, float]] = {}
    for cell, v in evaluables.items():
        c_lo, c_hi, c_p = bootstrap_expectancy(v, resamples=5000)
        stats[cell] = (len(v), sum(v) / len(v), c_lo, c_hi, c_p)

    keys = list(stats)
    accepted = benjamini_hochberg([stats[c][4] for c in keys], q=0.10)
    surv = {c for c, a in zip(keys, accepted, strict=True) if a}

    # FDR también en el lado PERDEDOR. El p-valor de `bootstrap_expectancy` es
    # unilateral ("evidencia contra que esta celda gane"), así que una celda
    # perdedora nunca sale aceptada arriba. Sin esta segunda corrección,
    # desactivar una estrategia por su IC suelto es el mismo error que
    # activarla: con ~28 celdas, alguna parece perdedora por azar.
    losers: dict[tuple[str, str], float] = {}
    for cell, v in evaluables.items():
        losers[cell] = bootstrap_expectancy([-x for x in v], resamples=5000)[2]
    lose_ok = benjamini_hochberg([losers[c] for c in keys], q=0.10)
    surv_lose = {c for c, a in zip(keys, lose_ok, strict=True) if a and stats[c][1] < 0}

    print()
    print("=" * 74)
    print(
        f"CONDICIONES OBSERVABLES AL ENTRAR  ({len(evaluables)} celdas, "
        f"{small} descartadas por n<{MIN_N})"
    )
    print("=" * 74)
    print(f"{'dimension':<13}{'valor':<20}{'ops':>6}{'exp R':>9}{'IC 95%':>22}{'FDR':>7}")
    print("-" * 77)
    for cell in sorted(keys, key=lambda c: -stats[c][1]):
        dim, val = cell
        n, e, c_lo, c_hi, _ = stats[cell]
        ci = f"[{c_lo:+.3f}, {c_hi:+.3f}]"
        if cell in surv:
            mark = "GANA"
        elif cell in surv_lose:
            mark = "PIERDE"
        else:
            mark = "-"
        print(f"{dim:<13}{val:<20}{n:>6}{e:>+9.3f}{ci:>22}{mark:>7}")

    print()
    winners = [c for c in surv if stats[c][2] > 0]
    if winners:
        print(f"SOBREVIVEN AL FDR COMO GANADORAS: {len(winners)}")
        for cell in winners:
            print(f"  {cell[0]}={cell[1]}  n={stats[cell][0]}  exp={stats[cell][1]:+.3f}R")
    else:
        print("NINGUNA celda sobrevive al FDR como ganadora.")

    print()
    if surv_lose:
        print(f"SOBREVIVEN AL FDR COMO PERDEDORAS: {len(surv_lose)}")
        for cell in sorted(surv_lose, key=lambda c: stats[c][1]):
            print(f"  {cell[0]}={cell[1]}  n={stats[cell][0]}  exp={stats[cell][1]:+.3f}R")
    else:
        print("NINGUNA celda sobrevive al FDR como perdedora.")

    print()
    print("Ambas listas son IN-SAMPLE: las celdas se eligieron mirando estos")
    print("mismos datos. Antes de cablear o desactivar nada, pasar por")
    print("`journal_walk_forward.py`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

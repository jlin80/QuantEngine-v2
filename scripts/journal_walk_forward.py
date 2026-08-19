"""Walk-forward de la seleccion de celdas sobre el journal REAL.

La segmentacion in-sample encontro celdas que sobreviven al FDR. Eso NO basta:
las celdas se eligieron mirando los mismos datos con los que se evaluan, que es
la definicion de sobreajuste. La unica prueba que vale es si una decision tomada
con datos pasados sobrevive a datos que no vio.

Protocolo, fijado antes de mirar el resultado:
  - El journal se ordena por cierre y se parte en 4 bloques contiguos.
  - Para cada frontera: in-sample = todo lo anterior, out-of-sample = bloque
    siguiente (que no se vuelve a tocar).
  - En el in-sample se aplica EL MISMO criterio que la segmentacion: n>=30,
    IC inferior sobre cero y superviviente de Benjamini-Hochberg (q=0.10).
  - Despues se mide, SIN volver a elegir, que hicieron esas celdas en el OOS.

Un conjunto de seleccion vacio es un resultado, no un fallo.
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
FOLDS = 3
RESAMPLES = 3000


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
    rows.sort(key=lambda r: str(r.get("exit_time") or ""))
    return rows


def dims(r: dict[str, Any]) -> dict[str, str]:
    return {
        "strategy": str(r.get("strategy") or "?"),
        "categoria": str(r.get("strategy_category") or "?"),
        "regimen": str(r.get("regime") or "?"),
        "volatilidad": str(r.get("volatility") or "?"),
        "lado": str(r.get("side") or "?"),
    }


def select(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Celdas que el criterio habria promovido con SOLO estos datos."""
    cells: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for r in rows:
        rv = float(r.get("r_multiple", 0) or 0)
        for d, v in dims(r).items():
            cells[(d, v)].append(rv)
    ev = {k: v for k, v in cells.items() if len(v) >= MIN_N}
    if not ev:
        return set()
    keys = list(ev)
    stats = {k: bootstrap_expectancy(ev[k], resamples=RESAMPLES) for k in keys}
    ok = benjamini_hochberg([stats[k][2] for k in keys], q=0.10)
    return {k for k, a in zip(keys, ok, strict=True) if a and stats[k][0] > 0}


def main() -> int:
    path = Path(sys.argv[1])
    symbol = sys.argv[2] if len(sys.argv) > 2 else "XAUUSDM"
    rows = load(path, symbol)
    blocks = FOLDS + 1
    size = len(rows) // blocks
    print(f"{symbol}: {len(rows)} operaciones, {blocks} bloques de ~{size}\n")

    total_oos: list[float] = []
    for fold in range(1, blocks):
        is_rows = rows[: fold * size]
        oos_rows = rows[fold * size : (fold + 1) * size]
        chosen = select(is_rows)
        print(f"--- pliegue {fold} | IS={len(is_rows)} ops, OOS={len(oos_rows)} ops")
        if not chosen:
            print("    seleccion IN-SAMPLE: VACIA -> no hay decision que validar\n")
            continue
        print(f"    seleccion IN-SAMPLE: {', '.join(f'{d}={v}' for d, v in sorted(chosen))}")
        oos: list[float] = []
        for r in oos_rows:
            dd = dims(r)
            if any((d, v) in chosen for d, v in dd.items()):
                oos.append(float(r.get("r_multiple", 0) or 0))
        if len(oos) < MIN_N:
            print(f"    OOS: solo {len(oos)} operaciones, muestra insuficiente\n")
            continue
        e = sum(oos) / len(oos)
        lo, hi, _ = bootstrap_expectancy(oos, resamples=RESAMPLES)
        veredicto = "CONFIRMA" if lo > 0 else ("mantiene signo" if e > 0 else "FALLA")
        print(f"    OOS: n={len(oos)}  exp={e:+.4f}R  IC[{lo:+.4f}, {hi:+.4f}]  -> {veredicto}\n")
        total_oos.extend(oos)

    print("=" * 66)
    if not total_oos:
        print("Ningun pliegue produjo operaciones OOS evaluables.")
        return 0
    e = sum(total_oos) / len(total_oos)
    lo, hi, p = bootstrap_expectancy(total_oos, resamples=5000)
    print(
        f"OOS AGREGADO: n={len(total_oos)}  exp={e:+.4f}R  IC 95% [{lo:+.4f}, {hi:+.4f}]  p={p:.4f}"
    )
    if lo > 0:
        print("El IC inferior esta sobre cero: la seleccion sobrevive fuera de muestra.")
    elif e > 0:
        print("Positivo pero el IC toca el cero: compatible con no tener ventaja.")
    else:
        print("Negativo fuera de muestra: la seleccion era ajuste al pasado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

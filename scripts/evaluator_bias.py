"""Cuánto se desvía el evaluador continuo de la ejecución real, por estrategia.

El evaluador continuo (Fase 4) resuelve cada señal contra sus propios TP/SL con
las velas posteriores. **No pasa por el Execution Engine**: no hay sizing, ni
spread, ni salida por régimen, ni límites de riesgo. Su expectativa es la de la
señal en el vacío.

Eso no sería un problema si fuera sólo informativo, pero **el Meta Strategy
Manager gobierna los pesos con esa evidencia** — la virtual pesa
``1 - trades/min_trades`` mientras la estrategia tiene poca muestra ejecutada.
Si el número virtual está inflado, el gobierno premia rendimiento que no existe.

Que es optimista ya estaba documentado. Lo que faltaba era **cuánto**, y si el
sesgo es parejo entre estrategias o cambia el orden entre ellas — porque un
sesgo constante no rompe un ranking y uno desigual sí.

Uso (en la VPS):
    python -m scripts.evaluator_bias --desde 2026-08-11
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

_MIN_EXECUTED = 20


def _executed(path: Path, symbol: str, desde: str) -> dict[str, list[float]]:
    """R por estrategia de las operaciones realmente ejecutadas."""
    rows: dict[str, list[float]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if symbol and str(record.get("symbol", "")).upper() != symbol.upper():
                continue
            if str(record.get("entry_time") or "")[:10] < desde:
                continue
            name = str(record.get("strategy") or "")
            r = record.get("r_multiple")
            if not name or r is None:
                continue
            rows[name].append(float(r))
    return rows


def _virtual(api: str) -> dict[str, tuple[int, float]]:
    """(señales, expectativa) por estrategia según el evaluador continuo."""
    with urlopen(f"{api}/api/engine/performance", timeout=15) as response:
        payload = json.load(response)
    return {
        name: (int(row.get("evaluated", 0)), float(row.get("expectancy_r", 0.0)))
        for name, row in payload.get("strategies", {}).items()
    }


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Sesgo del evaluador continuo vs ejecucion")
    parser.add_argument("--journal", default="data/execution/journal.jsonl")
    parser.add_argument("--symbol", default="XAUUSDM")
    parser.add_argument("--desde", default="2026-08-11")
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    executed = _executed(Path(args.journal), args.symbol, args.desde)
    virtual = _virtual(args.api)

    filas: list[tuple[str, int, float, int, float, float]] = []
    sin_virtual: list[tuple[str, int, float]] = []
    for name, values in executed.items():
        if len(values) < _MIN_EXECUTED:
            continue
        exp_real = sum(values) / len(values)
        entrada = virtual.get(name)
        if entrada is None or entrada[0] == 0:
            # Estrategia apagada en el loader: no emite señales, asi que no hay
            # evidencia virtual con la que comparar. Se declara aparte en vez de
            # colarla como un cero o un nan que envenene los agregados.
            sin_virtual.append((name, len(values), exp_real))
            continue
        señales, exp_virtual = entrada
        filas.append((name, señales, exp_virtual, len(values), exp_real, exp_virtual - exp_real))

    if not filas:
        print("Sin estrategias con muestra ejecutada suficiente.")
        return

    filas.sort(key=lambda f: -f[5])
    header = (
        f"{'estrategia':<24}{'señales':>8}{'virtual':>10}"
        f"{'ejecutadas':>12}{'real':>10}{'sesgo':>10}"
    )
    print(f"Sesgo del evaluador continuo — {args.symbol}, desde {args.desde}")
    print()
    print(header)
    print("-" * len(header))
    for name, señales, exp_virtual, n_real, exp_real, sesgo in filas:
        print(
            f"{name:<24}{señales:>8}{exp_virtual:>+10.4f}"
            f"{n_real:>12}{exp_real:>+10.4f}{sesgo:>+10.4f}"
        )

    if sin_virtual:
        print()
        print("Sin evidencia virtual (apagadas en el loader, no emiten señales):")
        for name, n_real, exp_real in sorted(sin_virtual):
            print(f"  {name:<24} ejecutadas={n_real:<6} real={exp_real:+.4f}")

    sesgos = [f[5] for f in filas]
    print()
    print(f"Sesgo medio:    {sum(sesgos) / len(sesgos):+.4f}R")
    print(f"Sesgo mediano:  {st.median(sesgos):+.4f}R")
    if len(sesgos) > 1:
        print(f"Desviacion:     {st.stdev(sesgos):.4f}R")
    print(f"Rango:          {min(sesgos):+.4f}R a {max(sesgos):+.4f}R")
    print()
    # Un sesgo constante desplaza a todas por igual y no rompe un ranking; uno
    # desigual reordena, y entonces el gobierno de pesos elige mal.
    orden_virtual = [f[0] for f in sorted(filas, key=lambda f: -f[2])]
    orden_real = [f[0] for f in sorted(filas, key=lambda f: -f[4])]
    print("Orden por expectativa VIRTUAL:", " > ".join(orden_virtual))
    print("Orden por expectativa REAL:   ", " > ".join(orden_real))
    print()
    if orden_virtual == orden_real:
        print("El orden se conserva: el sesgo desplaza, no reordena.")
    else:
        movidas = sum(1 for a, b in zip(orden_virtual, orden_real, strict=True) if a != b)
        print(
            f"El orden CAMBIA en {movidas} de {len(filas)} posiciones: el sesgo no es "
            "constante, asi que reordena el ranking que gobierna los pesos."
        )


if __name__ == "__main__":
    main()

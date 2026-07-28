"""Compara el rendimiento del bot entre dos ventanas temporales del journal.

Sirve para juzgar si un cambio sirvio, sin mirar Discord a ojo. Todas las
metricas de comparacion son **escala-invariante** (R-multiples, win rate, profit
factor), asi que un cambio de balance entre ventanas no las distorsiona: el PnL
en dolares se muestra aparte y solo como referencia.

Uso:
    python scripts/compare_periods.py --cut 2026-07-27T21:20
    python scripts/compare_periods.py --cut 2026-07-28T18:00 --symbol ETHUSDM
    python scripts/compare_periods.py --last-hours 24
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_JOURNAL = Path("data/execution/journal.jsonl")


@dataclass(frozen=True, slots=True)
class Metrics:
    """Resumen de una ventana temporal."""

    n: int
    win_rate: float
    profit_factor: float
    expectancy_r: float
    median_duration_s: float
    pnl: float
    exits: Counter[str]

    @property
    def is_empty(self) -> bool:
        """Whether the window holds no trades."""
        return self.n == 0


def load(path: Path) -> list[dict[str, Any]]:
    """Read the Trade Journal, skipping unreadable lines."""
    rows: list[dict[str, Any]] = []
    if not path.exists():
        raise SystemExit(f"No existe el journal: {path}")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def exit_time(row: dict[str, Any]) -> datetime | None:
    """Parse the trade's exit timestamp (naive UTC)."""
    raw = row.get("exit_time") or row.get("recorded_at")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def measure(rows: list[dict[str, Any]]) -> Metrics:
    """Compute the scale-invariant summary of a set of trades."""
    if not rows:
        return Metrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, Counter())
    pnls = [float(r.get("pnl") or 0.0) for r in rows]
    rs = [float(r.get("r_multiple") or 0.0) for r in rows]
    durations = sorted(float(r.get("duration_seconds") or 0.0) for r in rows)
    wins = sum(1 for p in pnls if p > 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    return Metrics(
        n=len(rows),
        win_rate=wins / len(rows) * 100.0,
        # Sin perdidas el profit factor es infinito; se reporta 0 para no mentir
        # con un numero enorme sobre una muestra diminuta.
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else 0.0,
        expectancy_r=statistics.mean(rs),
        median_duration_s=durations[len(durations) // 2],
        pnl=sum(pnls),
        exits=Counter(str(r.get("exit_reason")) for r in rows),
    )


def _delta(before: float, after: float, *, higher_is_better: bool = True) -> str:
    """Format a metric delta with a direction marker."""
    diff = after - before
    if abs(diff) < 1e-9:
        return "     ="
    good = (diff > 0) if higher_is_better else (diff < 0)
    return f"{'+' if diff > 0 else ''}{diff:.3f} {'OK' if good else '!!'}"


def report(before: Metrics, after: Metrics, label_a: str, label_b: str) -> None:
    """Print the side-by-side comparison."""
    print(f"\n{'':<22}{label_a:>20}{label_b:>20}{'  cambio':>14}")
    print("-" * 78)
    if before.is_empty or after.is_empty:
        print("  Una de las ventanas esta vacia: no hay comparacion posible.")
        print(f"  n = {before.n} vs {after.n}")
        return

    rows = [
        ("operaciones", before.n, after.n, None),
        ("win rate %", before.win_rate, after.win_rate, True),
        ("profit factor", before.profit_factor, after.profit_factor, True),
        ("expectancy (R)", before.expectancy_r, after.expectancy_r, True),
        ("duracion mediana s", before.median_duration_s, after.median_duration_s, True),
        ("pnl (USD, ref.)", before.pnl, after.pnl, True),
    ]
    for name, a, b, higher in rows:
        change = "" if higher is None else _delta(float(a), float(b), higher_is_better=higher)
        print(f"{name:<22}{a:>20.3f}{b:>20.3f}{change:>14}")

    print(f"\n{'motivo de salida':<22}{label_a:>20}{label_b:>20}")
    print("-" * 62)
    for reason in sorted(set(before.exits) | set(after.exits)):
        pa = before.exits.get(reason, 0) / before.n * 100
        pb = after.exits.get(reason, 0) / after.n * 100
        print(
            f"{reason:<22}"
            f"{before.exits.get(reason, 0):>8} ({pa:5.1f}%)"
            f"{after.exits.get(reason, 0):>8} ({pb:5.1f}%)"
        )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--cut", help="Corte ISO, p. ej. 2026-07-27T21:20")
    parser.add_argument(
        "--last-hours",
        type=float,
        help="Compara las ultimas N horas contra todo lo anterior.",
    )
    parser.add_argument("--symbol", help="Filtra por simbolo (ETHUSDM, USTECM...)")
    args = parser.parse_args()

    rows = load(args.journal)
    for row in rows:
        row["_ts"] = exit_time(row)
    rows = [r for r in rows if r["_ts"] is not None]
    if args.symbol:
        rows = [r for r in rows if str(r.get("symbol", "")).upper() == args.symbol.upper()]
    if not rows:
        raise SystemExit("El journal no tiene operaciones que cumplan el filtro.")
    rows.sort(key=lambda r: r["_ts"])

    if args.last_hours:
        cut = rows[-1]["_ts"] - timedelta(hours=args.last_hours)
    elif args.cut:
        cut = datetime.fromisoformat(args.cut)
    else:
        raise SystemExit("Indica --cut o --last-hours.")

    scope = f" [{args.symbol.upper()}]" if args.symbol else ""
    print(f"Journal{scope}: {len(rows)} operaciones  "
          f"({rows[0]['_ts']:%Y-%m-%d %H:%M}  ->  {rows[-1]['_ts']:%Y-%m-%d %H:%M})")
    print(f"Corte: {cut:%Y-%m-%d %H:%M}")

    before = measure([r for r in rows if r["_ts"] < cut])
    after = measure([r for r in rows if r["_ts"] >= cut])
    report(before, after, "ANTES", "DESPUES")

    if not before.is_empty and not after.is_empty and min(before.n, after.n) < 30:
        print(
            f"\nAVISO: la ventana mas pequena tiene {min(before.n, after.n)} operaciones. "
            "Con menos de ~30 el ruido domina: no saques conclusiones todavia."
        )


if __name__ == "__main__":
    main()

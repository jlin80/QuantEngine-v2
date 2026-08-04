"""Mide el hueco entre el Trade Journal real y los criterios de graduación a live.

Uso:
    python scripts/graduation_gap.py data/execution/journal.jsonl
    python scripts/graduation_gap.py journal.jsonl --since 2026-08-04 --equity 500

No toca nada: lee un fichero y escribe un informe. **Cumplir los criterios no
activa live** — la habilitación sigue siendo una decisión manual con el guard
anti-live intacto.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.execution.models.trades import TradeRecord
from app.production.live.graduation import evaluate_graduation


def load_journal(path: Path, since: datetime | None) -> list[TradeRecord]:
    """Read a Trade Journal JSONL, skipping unreadable lines.

    Args:
        path: Fichero JSONL del journal.
        since: Sólo operaciones con entrada posterior (``None`` = todas).

    Returns:
        Las operaciones legibles.
    """
    trades: list[TradeRecord] = []
    skipped = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                trade = TradeRecord.from_dict(json.loads(line))
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            if since is None or trade.entry_time >= since:
                trades.append(trade)
    if skipped:
        print(f"  (se omitieron {skipped} líneas ilegibles)")
    return trades


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="Ruta al journal.jsonl")
    parser.add_argument("--since", help="Sólo operaciones desde esta fecha (YYYY-MM-DD)")
    parser.add_argument("--equity", type=float, default=500.0, help="Equity de partida")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args()

    if not args.journal.exists():
        print(f"No existe: {args.journal}", file=sys.stderr)
        return 1

    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC) if args.since else None
    trades = load_journal(args.journal, since)
    report = evaluate_graduation(trades, starting_equity=args.equity)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(f"\nCriterios de graduación a live — {len(trades)} operaciones\n")
    for criterion in report.criteria:
        mark = "OK  " if criterion.passed else "FALTA"
        print(f"  [{mark}] {criterion.description}")
        print(f"          objetivo {criterion.target}   real {criterion.actual}")
        if criterion.gap:
            print(f"          -> {criterion.gap}")
    print()
    if report.met:
        print("Todos los criterios se cumplen.")
        print("Esto NO activa live: la habilitación sigue siendo una decisión tuya,")
        print("manual y explícita, con el guard anti-live intacto.")
    else:
        print(f"Pendientes: {len(report.pending)} de {len(report.criteria)} criterios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

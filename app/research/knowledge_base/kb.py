"""Base de conocimiento append-only del laboratorio (Fase 10)."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.research.models import CandidateReport, StrategyGenome, new_id
from app.utils.time import isoformat_utc, utc_now


class KnowledgeBase:
    """Append-only store of research knowledge (never deletes).

    Args:
        directory: Carpeta de persistencia; ``None`` mantiene todo en memoria.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = directory / "knowledge.jsonl" if directory is not None else None
        self._entries: list[dict[str, Any]] = []
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        """Load persisted knowledge entries."""
        if self._path is None or not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                self._entries.append(json.loads(line))

    def record(
        self,
        *,
        genome_id: str,
        name: str,
        symbol: str,
        outcome: str,
        reason: str,
        regime: str = "",
        parameters: dict[str, Any] | None = None,
        metrics: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Record one piece of knowledge.

        Args:
            genome_id: Genoma asociado.
            name: Nombre de la estrategia.
            symbol: Mercado.
            outcome: ``worked`` | ``failed`` | ``inconclusive``.
            reason: Por qué (la lección aprendida).
            regime: Régimen/contexto en que se observó.
            parameters: Parámetros del genoma.
            metrics: Métricas destacadas.

        Returns:
            La entrada guardada (JSON-safe).
        """
        entry: dict[str, Any] = {
            "id": new_id("kb"),
            "genome_id": genome_id,
            "name": name,
            "symbol": symbol,
            "outcome": outcome,
            "reason": reason,
            "regime": regime,
            "parameters": parameters or {},
            "metrics": metrics or {},
            "recorded_at": isoformat_utc(utc_now()),
        }
        self._entries.append(entry)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        return entry

    def record_candidate(self, report: CandidateReport, genome: StrategyGenome) -> dict[str, Any]:
        """Record the outcome of a candidate evaluation as knowledge."""
        outcome = "worked" if report.passed else "failed"
        reason = (
            report.reasons[0] if report.reasons else ("aprobada" if report.passed else "rechazada")
        )
        return self.record(
            genome_id=report.genome_id,
            name=report.name,
            symbol=report.symbol,
            outcome=outcome,
            reason=reason,
            regime=str(genome.metadata.get("theme", "")),
            parameters=genome.parameters(),
            metrics={
                k: report.statistics[k]
                for k in ("profit_factor", "sharpe", "expectancy_r", "max_drawdown_pct")
                if k in report.statistics
            },
        )

    def query(
        self, *, symbol: str | None = None, outcome: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return knowledge entries, newest first, optionally filtered."""
        entries = list(reversed(self._entries))
        if symbol is not None:
            entries = [e for e in entries if e["symbol"] == symbol.upper() or e["symbol"] == symbol]
        if outcome is not None:
            entries = [e for e in entries if e["outcome"] == outcome]
        return entries[:limit]

    def count(self) -> int:
        """Total number of knowledge entries."""
        return len(self._entries)

    def summary(self) -> dict[str, Any]:
        """Aggregate view: counts by outcome and by symbol, top lessons."""
        outcomes = Counter(e["outcome"] for e in self._entries)
        symbols = Counter(e["symbol"] for e in self._entries)
        reasons = Counter(e["reason"] for e in self._entries)
        return {
            "total": len(self._entries),
            "by_outcome": dict(outcomes),
            "by_symbol": dict(symbols.most_common(10)),
            "top_reasons": [{"reason": r, "count": c} for r, c in reasons.most_common(8)],
        }

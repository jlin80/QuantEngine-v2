"""Modelo y registro append-only de candidatas a producción (Fase 10)."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.exceptions import ResearchError
from app.research.models import (
    CandidateReport,
    CandidateStatus,
    PaperTrialStatus,
    PromotionDecision,
    StrategyGenome,
    candidate_report_from_dict,
    genome_from_dict,
)
from app.utils.time import isoformat_utc, utc_now


@dataclass(kw_only=True, slots=True)
class ProductionCandidate:
    """A strategy that passed the pipeline and its promotion lifecycle."""

    genome: StrategyGenome
    report: CandidateReport
    status: CandidateStatus
    paper: PaperTrialStatus | None = None
    promotion: PromotionDecision | None = None
    registered_at: str = field(default_factory=lambda: isoformat_utc(utc_now()))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "genome": self.genome.to_dict(),
            "report": self.report.to_dict(),
            "status": self.status.value,
            "paper": self.paper.to_dict() if self.paper else None,
            "promotion": self.promotion.to_dict() if self.promotion else None,
            "registered_at": self.registered_at,
        }


class CandidateStore:
    """Append-only registry of production candidates (never deletes).

    Persiste un resumen por candidata y mantiene el objeto tipado en memoria. La
    última versión de cada genoma gana; el histórico completo queda en el log.

    Args:
        directory: Carpeta de persistencia; ``None`` = sólo memoria.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = directory / "candidates.jsonl" if directory is not None else None
        self._candidates: dict[str, ProductionCandidate] = {}
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        """Replay the log; the latest version of each genome wins."""
        if self._path is None or not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                self._candidates[json.loads(line)["genome"]["id"]] = _from_dict(json.loads(line))

    def _append(self, candidate: ProductionCandidate) -> None:
        """Persist a candidate and index it as the latest version."""
        self._candidates[candidate.genome.id] = candidate
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(candidate.to_dict(), default=str) + "\n")

    def register(self, genome: StrategyGenome, report: CandidateReport) -> ProductionCandidate:
        """Register a new candidate (status ``candidate``)."""
        candidate = ProductionCandidate(
            genome=genome, report=report, status=CandidateStatus.CANDIDATE
        )
        self._append(candidate)
        return candidate

    def get(self, genome_id: str) -> ProductionCandidate:
        """Return a candidate by genome id.

        Raises:
            ResearchError: Si el genoma no está registrado.
        """
        candidate = self._candidates.get(genome_id)
        if candidate is None:
            raise ResearchError(
                f"Candidata no registrada: {genome_id}", context={"genome_id": genome_id}
            )
        return candidate

    def set_status(self, genome_id: str, status: CandidateStatus) -> ProductionCandidate:
        """Transition a candidate's status (append a new version)."""
        current = self.get(genome_id)
        current.status = status
        self._append(current)
        return current

    def attach_paper(self, genome_id: str, paper: PaperTrialStatus) -> ProductionCandidate:
        """Attach the paper-validation status to a candidate."""
        current = self.get(genome_id)
        current.paper = paper
        current.status = CandidateStatus.PAPER
        self._append(current)
        return current

    def attach_promotion(self, genome_id: str, decision: PromotionDecision) -> ProductionCandidate:
        """Attach a promotion decision and update the status accordingly."""
        current = self.get(genome_id)
        current.promotion = decision
        current.status = CandidateStatus.PROMOTED if decision.approved else CandidateStatus.REJECTED
        self._append(current)
        return current

    def list(self, *, status: CandidateStatus | None = None) -> list[ProductionCandidate]:
        """List candidates, newest first, optionally filtered by status."""
        candidates = sorted(self._candidates.values(), key=lambda c: c.registered_at, reverse=True)
        if status is not None:
            candidates = [c for c in candidates if c.status is status]
        return candidates

    def count(self) -> int:
        """Number of distinct candidates."""
        return len(self._candidates)


def _from_dict(data: dict[str, Any]) -> ProductionCandidate:
    """Rebuild a ProductionCandidate from its serialized form."""
    paper = None
    if data.get("paper"):
        p = data["paper"]
        paper = PaperTrialStatus(
            genome_id=p["genome_id"],
            matured=bool(p["matured"]),
            days=float(p["days"]),
            trades=int(p["trades"]),
            profit_factor=float(p["profit_factor"]),
            drawdown_pct=float(p["drawdown_pct"]),
            reasons=tuple(p.get("reasons", [])),
        )
    promotion = None
    if data.get("promotion"):
        pr = data["promotion"]
        promotion = PromotionDecision(
            genome_id=pr["genome_id"],
            name=pr["name"],
            approved=bool(pr["approved"]),
            operator=pr.get("operator", ""),
            drift=float(pr.get("drift", 0.0)),
            improvement=float(pr.get("improvement", 0.0)),
            reasons=tuple(pr.get("reasons", [])),
            blockers=tuple(pr.get("blockers", [])),
        )
    return ProductionCandidate(
        genome=genome_from_dict(data["genome"]),
        report=candidate_report_from_dict(data["report"]),
        status=CandidateStatus(data["status"]),
        paper=paper,
        promotion=promotion,
        registered_at=data.get("registered_at", isoformat_utc(utc_now())),
    )

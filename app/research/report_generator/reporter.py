"""Generación de informes del laboratorio (Fase 10)."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.research.models import (
    CandidateReport,
    RankingEntry,
    ShadowComparison,
)
from app.utils.time import isoformat_utc, utc_now


class ResearchReporter:
    """Build and persist laboratory reports (JSON + Markdown)."""

    def candidate_report(self, report: CandidateReport) -> dict[str, Any]:
        """Structured report of a candidate's pipeline run."""
        return {
            "type": "candidate",
            "generated_at": isoformat_utc(utc_now()),
            "candidate": report.to_dict(),
            "summary": self._candidate_summary(report),
        }

    def ranking_report(
        self, entries: Sequence[RankingEntry], *, segment: str = "global"
    ) -> dict[str, Any]:
        """Structured ranking report."""
        return {
            "type": "ranking",
            "generated_at": isoformat_utc(utc_now()),
            "segment": segment,
            "size": len(entries),
            "leader": entries[0].name if entries else "",
            "ranking": [entry.to_dict() for entry in entries],
        }

    def shadow_report(self, comparison: ShadowComparison) -> dict[str, Any]:
        """Structured Shadow Mode comparison report."""
        return {
            "type": "shadow",
            "generated_at": isoformat_utc(utc_now()),
            "comparison": comparison.to_dict(),
        }

    def to_markdown(self, report: dict[str, Any]) -> str:
        """Render a report dict as Markdown."""
        lines = [f"# Informe de investigación — {report.get('type', 'general')}", ""]
        lines.append(f"*Generado:* {report.get('generated_at', '')}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        return "\n".join(lines)

    def save(
        self,
        report: dict[str, Any],
        directory: Path,
        *,
        name: str,
        formats: Sequence[str] = ("json",),
    ) -> dict[str, Path]:
        """Write a report to disk in the requested formats.

        Args:
            report: Informe a persistir.
            directory: Carpeta de salida (se crea si no existe).
            name: Nombre base del archivo (sin extensión).
            formats: ``json`` y/o ``md``.

        Returns:
            Mapa formato → ruta escrita.
        """
        directory.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}
        if "json" in formats:
            path = directory / f"{name}.json"
            path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
            )
            written["json"] = path
        if "md" in formats:
            path = directory / f"{name}.md"
            path.write_text(self.to_markdown(report), encoding="utf-8")
            written["md"] = path
        return written

    @staticmethod
    def _candidate_summary(report: CandidateReport) -> str:
        """One-line verdict of a candidate report."""
        verdict = "APROBADA" if report.passed else "RECHAZADA"
        passed = sum(1 for stage in report.stages if stage.passed)
        return f"{verdict}: {passed}/{len(report.stages)} etapas superadas"

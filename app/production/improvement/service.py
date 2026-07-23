"""ImprovementService: ejecuta el análisis y registra las recomendaciones.

Corre el :class:`ContinuousImprovementEngine`, deja constancia en la auditoría y
—si Notion está habilitado— documenta la lista priorizada. No modifica código:
sólo produce y registra recomendaciones para futuras versiones.
"""

import logging

from app.documentation.models import EntryCategory, JournalEntry
from app.documentation.service import DocumentationService
from app.production.audit import AuditAction, AuditLog
from app.production.improvement.engine import ContinuousImprovementEngine, ImprovementReport

_log = logging.getLogger("app.production.improvement")


class ImprovementService:
    """Run continuous-improvement analysis and register the recommendations.

    Args:
        engine: The improvement engine.
        audit: Audit log (recommendations are always recorded).
        documentation: Documentation service (Notion/Markdown), or ``None``.
        report_to_docs: Whether to write the report to the documentation layer.
    """

    def __init__(
        self,
        engine: ContinuousImprovementEngine,
        audit: AuditLog | None = None,
        documentation: DocumentationService | None = None,
        *,
        report_to_docs: bool = True,
    ) -> None:
        self._engine = engine
        self._audit = audit
        self._documentation = documentation
        self._report_to_docs = report_to_docs

    async def run(self) -> ImprovementReport:
        """Analyze, audit and document the improvement opportunities."""
        report = self._engine.analyze()
        if self._audit is not None:
            self._audit.record(
                action=AuditAction.IMPROVEMENT_RECOMMENDED,
                actor="system",
                target="codebase",
                after={
                    "total": len(report.items),
                    "top": [item.to_dict() for item in report.top(5)],
                },
            )
        if self._report_to_docs and self._documentation is not None and report.items:
            await self._documentation.record(
                JournalEntry(
                    title=f"Mejoras propuestas ({len(report.items)})",
                    content=report.to_markdown(),
                    category=EntryCategory.NOTE,
                    tags=("continuous-improvement", "fase-9"),
                )
            )
        return report

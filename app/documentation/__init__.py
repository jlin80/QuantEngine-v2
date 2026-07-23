"""DocumentationService: bitácora del proyecto (Markdown hoy, Notion mañana)."""

from app.documentation.models import EntryCategory, JournalEntry
from app.documentation.service import DocumentationService

__all__ = ["DocumentationService", "EntryCategory", "JournalEntry"]

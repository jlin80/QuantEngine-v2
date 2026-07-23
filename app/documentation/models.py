"""Modelos de la bitácora del proyecto."""

import enum
from dataclasses import dataclass, field
from datetime import datetime

from app.utils.time import utc_now


class EntryCategory(enum.StrEnum):
    """Clasificación de las entradas de bitácora."""

    DECISION = "decision"  # decisión técnica/arquitectónica
    MILESTONE = "milestone"  # hito de fase
    INCIDENT = "incident"  # incidente operativo
    NOTE = "note"  # nota general


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One project-journal entry.

    Attributes:
        title: Short headline.
        content: Markdown body.
        category: Entry classification.
        tags: Free-form labels.
        timestamp: UTC creation time.
    """

    title: str
    content: str
    category: EntryCategory = EntryCategory.NOTE
    tags: tuple[str, ...] = ()
    timestamp: datetime = field(default_factory=utc_now)

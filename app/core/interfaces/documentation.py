"""Contrato de backends de documentación (bitácora del proyecto).

Fase 1 escribe a Markdown local; una fase futura añadirá un backend Notion
implementando esta misma interfaz — el resto del sistema no cambiará.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.documentation.models import JournalEntry


@runtime_checkable
class DocumentationBackend(Protocol):
    """Destination for project journal entries."""

    @property
    def backend_name(self) -> str:
        """Short backend identifier (e.g. ``markdown``, ``notion``)."""
        ...

    async def record(self, entry: "JournalEntry") -> None:
        """Persist a journal entry.

        Raises:
            QuantEngineError: If the backend cannot persist the entry.
        """
        ...

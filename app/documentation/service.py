"""Servicio de documentación automática (bitácora del proyecto)."""

import logging
from typing import Any, Protocol, runtime_checkable

from app.core.interfaces.documentation import DocumentationBackend
from app.documentation.models import EntryCategory, JournalEntry


@runtime_checkable
class _Flushable(Protocol):
    """A backend with an offline sync queue that can be flushed."""

    async def flush(self) -> dict[str, int]: ...

    def pending(self) -> int: ...


class DocumentationService:
    """Fan-out of journal entries to registered backends.

    Igual que las notificaciones: *best effort* — un backend caído se
    registra en el log pero no interrumpe la operación.
    """

    def __init__(self) -> None:
        self._backends: list[DocumentationBackend] = []
        self._log = logging.getLogger("app.documentation")

    def register_backend(self, backend: DocumentationBackend) -> None:
        """Register a journal backend."""
        self._backends.append(backend)
        self._log.info("Documentation backend registered: %s", backend.backend_name)

    @property
    def backend_names(self) -> list[str]:
        """Names of the registered backends."""
        return [backend.backend_name for backend in self._backends]

    async def flush(self) -> dict[str, Any]:
        """Retry any queued entries in flushable backends (e.g. Notion).

        Returns:
            Per-backend flush result plus total pending, for the dashboard.
        """
        result: dict[str, Any] = {}
        pending = 0
        for backend in self._backends:
            if isinstance(backend, _Flushable):
                outcome = await backend.flush()
                result[backend.backend_name] = outcome
                pending += backend.pending()
        result["pending"] = pending
        return result

    def pending(self) -> int:
        """Total entries queued for retry across flushable backends."""
        return sum(b.pending() for b in self._backends if isinstance(b, _Flushable))

    async def record(self, entry: JournalEntry) -> None:
        """Persist an entry in every backend (best effort)."""
        for backend in self._backends:
            try:
                await backend.record(entry)
            except Exception:
                self._log.exception(
                    "Documentation backend '%s' failed for entry '%s'",
                    backend.backend_name,
                    entry.title,
                )

    async def record_decision(
        self, title: str, content: str, *, tags: tuple[str, ...] = ()
    ) -> None:
        """Shortcut for a DECISION entry."""
        await self.record(
            JournalEntry(title=title, content=content, category=EntryCategory.DECISION, tags=tags)
        )

    async def record_milestone(
        self, title: str, content: str, *, tags: tuple[str, ...] = ()
    ) -> None:
        """Shortcut for a MILESTONE entry."""
        await self.record(
            JournalEntry(title=title, content=content, category=EntryCategory.MILESTONE, tags=tags)
        )

"""Pruebas del servicio de documentación (bitácora Markdown)."""

from pathlib import Path

from app.documentation.backends import MarkdownJournalBackend
from app.documentation.service import DocumentationService


async def test_markdown_backend_appends_entries(tmp_path: Path):
    journal = tmp_path / "bitacora.md"
    service = DocumentationService()
    service.register_backend(MarkdownJournalBackend(journal))

    await service.record_decision(
        "Event Bus propio",
        "Se eligió un bus asyncio propio en vez de una librería.",
        tags=("arquitectura",),
    )
    await service.record_milestone("Fase 1 completa", "Infraestructura lista.")

    content = journal.read_text(encoding="utf-8")
    assert content.startswith("# Bitácora del proyecto")
    assert "Event Bus propio" in content
    assert "**Categoría:** decision" in content
    assert "`arquitectura`" in content
    assert "Fase 1 completa" in content
    assert "**Categoría:** milestone" in content


async def test_broken_backend_does_not_raise(tmp_path: Path):
    class _Broken:
        @property
        def backend_name(self) -> str:
            return "broken"

        async def record(self, entry) -> None:
            raise RuntimeError("backend down")

    service = DocumentationService()
    service.register_backend(_Broken())
    await service.record_decision("t", "c")  # no debe lanzar

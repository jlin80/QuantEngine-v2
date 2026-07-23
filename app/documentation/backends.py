"""Backends de la bitácora: Markdown (local) y Notion (API oficial).

El backend Notion escribe cada entrada como una página en una base de datos de
Notion. Si Notion no responde, la entrada **no se pierde**: se encola en disco
(``queue_path``) y ``flush`` la reintenta más tarde. Así la documentación
automática de la Fase 9 cumple la exigencia de "cola de sincronización" sin
bloquear jamás la operación.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from app.core.exceptions import ConfigurationError
from app.documentation.models import EntryCategory, JournalEntry
from app.utils.time import utc_now

_NOTION_API = "https://api.notion.com/v1/pages"
_MAX_BLOCK_CHARS = 1900  # Notion limita el texto enriquecido a 2000 por bloque
_MAX_BLOCKS = 90  # margen bajo el tope de 100 hijos por creación de página


class MarkdownJournalBackend:
    """Appends journal entries to a local Markdown file.

    Args:
        path: Target Markdown file (created on first write).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._log = logging.getLogger("app.documentation")

    @property
    def backend_name(self) -> str:
        """Backend identifier."""
        return "markdown"

    async def record(self, entry: JournalEntry) -> None:
        """Append the entry as a Markdown section."""
        tags = " ".join(f"`{t}`" for t in entry.tags)
        block = (
            f"\n## {entry.timestamp.strftime('%Y-%m-%d %H:%M UTC')} — {entry.title}\n\n"
            f"**Categoría:** {entry.category.value}"
            + (f" · **Tags:** {tags}" if tags else "")
            + f"\n\n{entry.content.strip()}\n"
        )
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                self._path.write_text(
                    "# Bitácora del proyecto — Quant Engine V2\n", encoding="utf-8"
                )
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(block)
        self._log.debug("Journal entry recorded: %s", entry.title)


class NotionJournalBackend:
    """Notion backend con cola de sincronización resiliente.

    Cada entrada se crea como una página en la base de datos configurada. La
    base sólo necesita una propiedad de título llamada ``Name`` (el nombre por
    defecto de Notion); el resto de metadatos (categoría, tags, contenido) va en
    el cuerpo de la página, así el backend no depende del esquema exacto.

    Args:
        api_key: Token de integración de Notion (secreto — nunca se loguea).
        database_id: Base de datos destino.
        version: Cabecera ``Notion-Version``.
        timeout_seconds: Timeout por petición.
        max_retries: Reintentos ante fallos transitorios (429/5xx/red).
        queue_path: Fichero JSONL donde se encolan entradas no entregadas.
        transport: Transporte httpx opcional (tests).
    """

    def __init__(
        self,
        api_key: str,
        database_id: str,
        *,
        version: str = "2022-06-28",
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        queue_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or not database_id:
            raise ConfigurationError(
                "Notion backend requires api_key and database_id",
                context={"has_key": bool(api_key), "has_db": bool(database_id)},
            )
        self._api_key = api_key
        self._database_id = database_id
        self._version = version
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._queue_path = queue_path
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._log = logging.getLogger("app.documentation")

    @property
    def backend_name(self) -> str:
        """Backend identifier."""
        return "notion"

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily build the shared HTTP client with auth headers."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Notion-Version": self._version,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _build_payload(self, entry: JournalEntry) -> dict[str, object]:
        """Map a journal entry to a Notion page-creation payload."""
        header = f"Categoría: {entry.category.value}"
        if entry.tags:
            header += " · Tags: " + ", ".join(entry.tags)
        header += f" · {entry.timestamp.strftime('%Y-%m-%d %H:%M UTC')}"
        chunks = [header, *_split_content(entry.content)]
        children = [_paragraph_block(chunk) for chunk in chunks[:_MAX_BLOCKS]]
        return {
            "parent": {"database_id": self._database_id},
            "properties": {
                "Name": {"title": [{"text": {"content": entry.title[:2000]}}]},
            },
            "children": children,
        }

    async def record(self, entry: JournalEntry) -> None:
        """Create the page in Notion; on failure, enqueue for later retry.

        Nunca lanza: un fallo de Notion no puede interrumpir la operación. La
        entrada queda encolada y ``flush`` la reintentará.
        """
        try:
            await self._send(entry)
        except Exception as exc:  # best effort: se encola y se sigue
            self._log.warning("Notion delivery failed, queueing entry '%s': %r", entry.title, exc)
            self._enqueue(entry)

    async def _send(self, entry: JournalEntry) -> None:
        """POST one page to Notion with retry/backoff.

        Raises:
            ConfigurationError: When Notion rejects the payload permanently or
                every retry is exhausted (the caller enqueues on this).
        """
        payload = self._build_payload(entry)
        client = self._get_client()
        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.post(_NOTION_API, json=payload)
            except httpx.HTTPError as exc:
                last_error = repr(exc)
                await asyncio.sleep(min(1.5 * attempt, 8.0))
                continue
            if response.status_code in (200, 201):
                self._log.debug("Notion page created for '%s'", entry.title)
                return
            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = f"HTTP {response.status_code}"
                await asyncio.sleep(min(1.5 * attempt, 8.0))
                continue
            raise ConfigurationError(
                "Notion rejected the entry",
                context={"status": response.status_code, "body": response.text[:300]},
            )
        raise ConfigurationError(
            "Notion delivery failed after retries",
            context={"attempts": self._max_retries, "error": last_error},
        )

    def _enqueue(self, entry: JournalEntry) -> None:
        """Append an undelivered entry to the on-disk queue (best effort)."""
        if self._queue_path is None:
            return
        record = {
            "title": entry.title,
            "content": entry.content,
            "category": entry.category.value,
            "tags": list(entry.tags),
            "timestamp": entry.timestamp.isoformat(),
        }
        try:
            self._queue_path.parent.mkdir(parents=True, exist_ok=True)
            with self._queue_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._log.error("Could not enqueue Notion entry: %r", exc)

    def pending(self) -> int:
        """Number of entries currently queued for retry."""
        if self._queue_path is None or not self._queue_path.exists():
            return 0
        try:
            with self._queue_path.open("r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0

    async def flush(self) -> dict[str, int]:
        """Retry every queued entry, keeping the ones that still fail.

        Returns:
            ``{"sent": int, "remaining": int}``.
        """
        if self._queue_path is None or not self._queue_path.exists():
            return {"sent": 0, "remaining": 0}
        async with self._lock:
            try:
                lines = [
                    line
                    for line in self._queue_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except OSError as exc:
                self._log.error("Could not read Notion queue: %r", exc)
                return {"sent": 0, "remaining": self.pending()}

            sent = 0
            still_failing: list[str] = []
            for line in lines:
                try:
                    entry = _entry_from_record(json.loads(line))
                except (ValueError, KeyError, TypeError):
                    continue  # una línea corrupta se descarta, no bloquea el resto
                try:
                    await self._send(entry)
                    sent += 1
                except Exception:  # sigue fallando: se reencola
                    still_failing.append(line)

            try:
                if still_failing:
                    self._queue_path.write_text("\n".join(still_failing) + "\n", encoding="utf-8")
                else:
                    self._queue_path.unlink(missing_ok=True)
            except OSError as exc:
                self._log.error("Could not rewrite Notion queue: %r", exc)
            return {"sent": sent, "remaining": len(still_failing)}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _split_content(content: str) -> list[str]:
    """Split a body into Notion-sized text chunks (≤ 2000 chars each)."""
    text = content.strip()
    if not text:
        return []
    return [text[i : i + _MAX_BLOCK_CHARS] for i in range(0, len(text), _MAX_BLOCK_CHARS)]


def _paragraph_block(text: str) -> dict[str, object]:
    """Build a Notion paragraph block from plain text."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def _entry_from_record(record: dict[str, object]) -> JournalEntry:
    """Rebuild a :class:`JournalEntry` from a queued record."""
    raw_ts = record.get("timestamp")
    timestamp = (
        datetime.fromisoformat(str(raw_ts)) if isinstance(raw_ts, str) and raw_ts else utc_now()
    )
    raw_tags = record.get("tags", [])
    tags = tuple(str(t) for t in raw_tags) if isinstance(raw_tags, list) else ()
    return JournalEntry(
        title=str(record["title"]),
        content=str(record.get("content", "")),
        category=EntryCategory(str(record.get("category", "note"))),
        tags=tags,
        timestamp=timestamp,
    )

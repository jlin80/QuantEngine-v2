"""Backend Notion: creación de página, encolado ante fallo y flush con reintento."""

import json

import httpx
from app.documentation.backends import NotionJournalBackend
from app.documentation.models import EntryCategory, JournalEntry


def _entry(title: str = "Hito") -> JournalEntry:
    return JournalEntry(
        title=title,
        content="Contenido de la entrada.",
        category=EntryCategory.MILESTONE,
        tags=("fase-9", "notion"),
    )


async def test_creates_page_with_title_and_children(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"id": "page-1"})

    backend = NotionJournalBackend(
        "secret-token",
        "db-123",
        queue_path=tmp_path / "queue.jsonl",
        transport=httpx.MockTransport(handler),
    )
    await backend.record(_entry("Fase 9 lista"))
    await backend.close()

    assert "notion.com/v1/pages" in captured["url"]
    assert captured["auth"] == "Bearer secret-token"
    payload = captured["payload"]
    assert payload["parent"]["database_id"] == "db-123"
    assert payload["properties"]["Name"]["title"][0]["text"]["content"] == "Fase 9 lista"
    assert payload["children"], "el contenido va como bloques hijos"
    assert backend.pending() == 0


async def test_failure_enqueues_and_flush_retries(tmp_path):
    state = {"fail": True, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(500 if state["fail"] else 200, json={})

    queue = tmp_path / "queue.jsonl"
    backend = NotionJournalBackend(
        "t", "db", queue_path=queue, max_retries=1, transport=httpx.MockTransport(handler)
    )

    # Notion caído: la entrada no se pierde, se encola y record NO lanza.
    await backend.record(_entry())
    assert backend.pending() == 1
    assert queue.exists()

    # Notion vuelve: flush la entrega y vacía la cola.
    state["fail"] = False
    result = await backend.flush()
    assert result == {"sent": 1, "remaining": 0}
    assert backend.pending() == 0
    await backend.close()


async def test_permanent_rejection_is_queued_not_raised(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    backend = NotionJournalBackend(
        "t", "db", queue_path=tmp_path / "q.jsonl", transport=httpx.MockTransport(handler)
    )
    await backend.record(_entry())  # no debe propagar el 400
    assert backend.pending() == 1
    await backend.close()

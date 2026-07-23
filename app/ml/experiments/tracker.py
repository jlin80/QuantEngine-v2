"""Registro de experimentos del ML (append-only, Fase 7).

Cada entrenamiento, AutoML, validación o decisión de deriva deja constancia. El
registro nunca se sobrescribe: es la memoria auditable del aprendizaje continuo.
Espeja el ``ExperimentManager`` del laboratorio (Fase 6).
"""

import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """A single append-only experiment entry."""

    id: str
    kind: str  # "training" | "automl" | "validation" | "drift" | "meta"
    label: str
    at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "at": self.at,
            "payload": self.payload,
        }


class MLExperimentTracker:
    """Append-only experiment log (memory + optional JSON Lines).

    Args:
        directory: Carpeta de persistencia (``None`` = sólo memoria).
        memory_limit: Máximo de experimentos retenidos en memoria.
    """

    def __init__(self, directory: Path | None = None, *, memory_limit: int = 500) -> None:
        self._dir = directory
        self._records: deque[ExperimentRecord] = deque(maxlen=memory_limit)
        self._log = logging.getLogger("app.ml.experiments")
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load()

    def save(self, kind: str, label: str, payload: dict[str, Any]) -> ExperimentRecord:
        """Append an experiment record (never overwrites)."""
        record = ExperimentRecord(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            label=label,
            at=utc_now().isoformat(),
            payload=payload,
        )
        self._records.append(record)
        if self._dir is not None:
            try:
                with (self._dir / "experiments.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            except OSError as exc:
                self._log.error("No se pudo persistir el experimento %s: %r", record.id, exc)
        return record

    def all(self) -> list[ExperimentRecord]:
        """Every retained experiment (oldest first)."""
        return list(self._records)

    def recent(self, limit: int = 50) -> list[ExperimentRecord]:
        """Most recent experiments (newest first)."""
        return list(reversed(self._records))[:limit]

    def count(self) -> int:
        """Number of retained experiments."""
        return len(self._records)

    def _load(self) -> None:
        """Load persisted experiments from disk (best-effort)."""
        if self._dir is None:
            return
        path = self._dir / "experiments.jsonl"
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                self._records.append(
                    ExperimentRecord(
                        id=str(data.get("id", uuid.uuid4().hex[:12])),
                        kind=str(data.get("kind", "unknown")),
                        label=str(data.get("label", "")),
                        at=str(data.get("at", utc_now().isoformat())),
                        payload=dict(data.get("payload", {})),
                    )
                )
        except (OSError, json.JSONDecodeError) as exc:
            self._log.error("No se pudieron leer los experimentos: %r", exc)

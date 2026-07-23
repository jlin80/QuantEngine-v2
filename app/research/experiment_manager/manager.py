"""Registro de experimentos append-only (Fase 10)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.exceptions import ExperimentNotFoundError
from app.research.models import (
    ExperimentRecord,
    ExperimentStatus,
    Hypothesis,
    new_id,
)


class ExperimentManager:
    """Append-only store of research experiments.

    Args:
        directory: Carpeta de persistencia; ``None`` mantiene todo en memoria.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = directory / "experiments.jsonl" if directory is not None else None
        self._records: dict[str, ExperimentRecord] = {}
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        """Replay the log; the latest version of each id wins."""
        if self._path is None or not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            self._records[data["id"]] = _from_dict(data)

    def _append(self, record: ExperimentRecord) -> None:
        """Persist a record and index it as the latest version."""
        self._records[record.id] = record
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), default=str) + "\n")

    def create(
        self,
        label: str,
        kind: str,
        *,
        hypothesis: Hypothesis | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ExperimentRecord:
        """Register a new open experiment.

        Args:
            label: Etiqueta legible.
            kind: Tipo (``generation``, ``optimization``, ``candidate``,
                ``shadow``, ``factor``, ``feature``...).
            hypothesis: Hipótesis de investigación (opcional).
            payload: Datos asociados (parámetros, contexto...).

        Returns:
            El experimento creado.
        """
        record = ExperimentRecord(
            id=new_id("exp"),
            label=label,
            kind=kind,
            status=ExperimentStatus.OPEN,
            hypothesis=hypothesis,
            payload=payload or {},
        )
        self._append(record)
        return record

    def get(self, experiment_id: str) -> ExperimentRecord:
        """Return an experiment by id.

        Raises:
            ExperimentNotFoundError: Si el id no existe.
        """
        record = self._records.get(experiment_id)
        if record is None:
            raise ExperimentNotFoundError(
                f"Experimento no encontrado: {experiment_id}",
                context={"experiment_id": experiment_id},
            )
        return record

    def update_payload(self, experiment_id: str, payload: dict[str, Any]) -> ExperimentRecord:
        """Merge extra data into an experiment's payload (append a new version)."""
        current = self.get(experiment_id)
        merged = {**current.payload, **payload}
        record = ExperimentRecord(
            id=current.id,
            label=current.label,
            kind=current.kind,
            status=current.status,
            hypothesis=current.hypothesis,
            payload=merged,
            conclusions=current.conclusions,
            created_at=current.created_at,
        )
        self._append(record)
        return record

    def close(self, experiment_id: str, conclusions: str) -> ExperimentRecord:
        """Close an experiment recording its conclusions."""
        return self._transition(experiment_id, ExperimentStatus.CLOSED, conclusions)

    def archive(self, experiment_id: str, conclusions: str = "") -> ExperimentRecord:
        """Archive an experiment (retained forever, never deleted)."""
        current = self._records.get(experiment_id)
        note = conclusions or (current.conclusions if current else "")
        return self._transition(experiment_id, ExperimentStatus.ARCHIVED, note)

    def _transition(
        self, experiment_id: str, status: ExperimentStatus, conclusions: str
    ) -> ExperimentRecord:
        """Append a status transition for an experiment."""
        current = self.get(experiment_id)
        record = ExperimentRecord(
            id=current.id,
            label=current.label,
            kind=current.kind,
            status=status,
            hypothesis=current.hypothesis,
            payload=current.payload,
            conclusions=conclusions or current.conclusions,
            created_at=current.created_at,
        )
        self._append(record)
        return record

    def list(
        self, *, kind: str | None = None, status: ExperimentStatus | None = None
    ) -> list[ExperimentRecord]:
        """List experiments, newest first, optionally filtered."""
        records = sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)
        if kind is not None:
            records = [r for r in records if r.kind == kind]
        if status is not None:
            records = [r for r in records if r.status is status]
        return records

    def count(self) -> int:
        """Number of distinct experiments."""
        return len(self._records)


def _from_dict(data: dict[str, Any]) -> ExperimentRecord:
    """Rebuild an ExperimentRecord from its serialized form."""
    hypothesis = None
    if data.get("hypothesis"):
        hyp = data["hypothesis"]
        hypothesis = Hypothesis(
            text=hyp.get("text", ""),
            rationale=hyp.get("rationale", ""),
            expected_edge=hyp.get("expected_edge", ""),
        )
    return ExperimentRecord(
        id=data["id"],
        label=data["label"],
        kind=data["kind"],
        status=ExperimentStatus(data["status"]),
        hypothesis=hypothesis,
        payload=data.get("payload", {}),
        conclusions=data.get("conclusions", ""),
        created_at=datetime.fromisoformat(data["created_at"]),
    )

"""Experiment Manager (Fase 6).

Registro permanente y append-only de cada experimento del laboratorio: id,
fecha, versión, commit, parámetros, dataset, resultado, tiempo, autor y notas.
Nunca sobrescribe un experimento anterior — cada uno es un archivo JSON propio,
para poder auditar la evolución del sistema y reproducir cualquier resultado.
"""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.utils.time import isoformat_utc, utc_now


@dataclass(frozen=True, slots=True)
class Experiment:
    """A single, immutable experiment record."""

    experiment_id: str
    label: str
    version: str
    commit: str
    dataset: str
    parameters: dict[str, Any]
    result: dict[str, Any]
    duration_seconds: float
    author: str
    notes: str
    created_at: str = field(default_factory=isoformat_utc)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "experiment_id": self.experiment_id,
            "label": self.label,
            "version": self.version,
            "commit": self.commit,
            "dataset": self.dataset,
            "parameters": self.parameters,
            "result": self.result,
            "duration_seconds": round(self.duration_seconds, 4),
            "author": self.author,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Experiment":
        """Rebuild an experiment from a serialized dict."""
        return cls(
            experiment_id=str(payload["experiment_id"]),
            label=str(payload.get("label", "")),
            version=str(payload.get("version", "")),
            commit=str(payload.get("commit", "")),
            dataset=str(payload.get("dataset", "")),
            parameters=dict(payload.get("parameters", {})),
            result=dict(payload.get("result", {})),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            author=str(payload.get("author", "")),
            notes=str(payload.get("notes", "")),
            created_at=str(payload.get("created_at", isoformat_utc())),
        )


class ExperimentManager:
    """Append-only store of experiments, one JSON file each.

    Args:
        directory: Carpeta donde se guardan los experimentos.
    """

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)

    def save(
        self,
        *,
        label: str,
        result: dict[str, Any],
        parameters: dict[str, Any] | None = None,
        dataset: str = "",
        version: str = "",
        commit: str = "uncommitted",
        duration_seconds: float = 0.0,
        author: str = "",
        notes: str = "",
    ) -> Experiment:
        """Persist a new experiment (never overwrites an existing one).

        Args:
            label: Etiqueta legible del experimento.
            result: Resultado serializable (estadística, informe...).
            parameters: Parámetros usados.
            dataset: Identificador del dataset.
            version: Versión del conjunto de parámetros/estrategia.
            commit: Commit de git (``uncommitted`` si no aplica).
            duration_seconds: Duración de la ejecución.
            author: Autor.
            notes: Notas libres.

        Returns:
            El experimento registrado.
        """
        experiment = Experiment(
            experiment_id=uuid.uuid4().hex,
            label=label,
            version=version,
            commit=commit,
            dataset=dataset,
            parameters=parameters or {},
            result=result,
            duration_seconds=duration_seconds,
            author=author,
            notes=notes,
            created_at=isoformat_utc(utc_now()),
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(experiment)
        path.write_text(
            json.dumps(experiment.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return experiment

    def _path_for(self, experiment: Experiment) -> Path:
        """Unique filename for an experiment (timestamp + short id)."""
        stamp = experiment.created_at.replace(":", "").replace("-", "").replace(".", "")[:15]
        return self._dir / f"{stamp}-{experiment.experiment_id[:8]}.json"

    def all(self) -> list[Experiment]:
        """Load every stored experiment, oldest first."""
        return sorted(self._iter_experiments(), key=lambda e: e.created_at)

    def _iter_experiments(self) -> Iterator[Experiment]:
        """Yield experiments parsed from the store directory."""
        if not self._dir.exists():
            return
        for file in self._dir.glob("*.json"):
            payload = json.loads(file.read_text(encoding="utf-8"))
            yield Experiment.from_dict(payload)

    def count(self) -> int:
        """Number of stored experiments."""
        return sum(1 for _ in self._dir.glob("*.json")) if self._dir.exists() else 0

"""Versionado de conjuntos de parámetros (Fase 6).

Cada conjunto de parámetros de una estrategia tiene una versión (``1.0``,
``1.1``, ``1.2``...). El :class:`VersionStore` guarda el historial completo por
estrategia y permite volver a una versión anterior — nunca se pierde una
configuración que funcionó.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.time import isoformat_utc


@dataclass(frozen=True, slots=True)
class ParameterSet:
    """A versioned set of strategy parameters."""

    strategy: str
    version: str
    parameters: dict[str, Any]
    notes: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "version": self.version,
            "parameters": self.parameters,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ParameterSet":
        """Rebuild a parameter set from a serialized dict."""
        return cls(
            strategy=str(payload["strategy"]),
            version=str(payload["version"]),
            parameters=dict(payload.get("parameters", {})),
            notes=str(payload.get("notes", "")),
            created_at=str(payload.get("created_at", isoformat_utc())),
        )


class VersionStore:
    """Persistent version history of strategy parameter sets.

    Args:
        directory: Carpeta donde se guarda un archivo por estrategia.
    """

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)

    def _path(self, strategy: str) -> Path:
        """File holding the version history of a strategy."""
        safe = strategy.replace("/", "_").replace("\\", "_")
        return self._dir / f"{safe}.json"

    def history(self, strategy: str) -> list[ParameterSet]:
        """Return every stored version of a strategy, oldest first."""
        path = self._path(strategy)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [ParameterSet.from_dict(item) for item in payload]

    def add(self, strategy: str, parameters: dict[str, Any], *, notes: str = "") -> ParameterSet:
        """Append a new version (auto-incrementing the minor number).

        Args:
            strategy: Nombre de la estrategia.
            parameters: Parámetros de la nueva versión.
            notes: Notas de la versión.

        Returns:
            El :class:`ParameterSet` recién creado.
        """
        history = self.history(strategy)
        version = self._next_version(history)
        entry = ParameterSet(
            strategy=strategy,
            version=version,
            parameters=parameters,
            notes=notes,
            created_at=isoformat_utc(),
        )
        history.append(entry)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(strategy).write_text(
            json.dumps([item.to_dict() for item in history], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return entry

    @staticmethod
    def _next_version(history: list[ParameterSet]) -> str:
        """Compute the next ``1.N`` version from the history."""
        if not history:
            return "1.0"
        minors = [int(item.version.split(".")[1]) for item in history if "." in item.version]
        return f"1.{(max(minors) + 1) if minors else 0}"

    def latest(self, strategy: str) -> ParameterSet | None:
        """Return the most recent version of a strategy (or ``None``)."""
        history = self.history(strategy)
        return history[-1] if history else None

    def get(self, strategy: str, version: str) -> ParameterSet | None:
        """Return a specific version of a strategy (or ``None``)."""
        for item in self.history(strategy):
            if item.version == version:
                return item
        return None

    def rollback(self, strategy: str, version: str) -> ParameterSet:
        """Re-add a previous version as the new latest (non-destructive rollback).

        Args:
            strategy: Nombre de la estrategia.
            version: Versión a la que volver.

        Returns:
            El nuevo :class:`ParameterSet` (copia de la versión pedida).

        Raises:
            KeyError: Si la versión no existe.
        """
        target = self.get(strategy, version)
        if target is None:
            raise KeyError(f"{strategy} no tiene la versión {version}")
        return self.add(strategy, dict(target.parameters), notes=f"rollback a v{version}")

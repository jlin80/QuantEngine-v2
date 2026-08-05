"""Persistencia append-only de las fotos de factores (Bloque 2).

Mismo contrato que el Trade Journal y el store de resultados virtuales: se
escribe por lotes, una línea corrupta no impide leer el resto, y un fallo de
disco nunca tumba a quien escribe.
"""

import json
import logging
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.engine.attribution.models import FactorSnapshot

_LOG = logging.getLogger("app.engine.attribution.store")


class FactorSnapshotStore:
    """Append-only ledger of decision-time factor vectors.

    Args:
        path: Fichero JSONL (``None`` desactiva el disco).
        persist: Si se escribe a disco.
        flush_size: Fotos acumuladas antes de volcar.
        memory_limit: Fotos recientes retenidas en memoria **aunque el disco
            esté desactivado**. Sin esto, apagar la persistencia dejaba al
            motor de atribución sin ninguna foto que unir y el informe salía
            vacío sin decir por qué: un ajuste de I/O no puede apagar en
            silencio una funcionalidad entera.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        persist: bool = True,
        flush_size: int = 25,
        memory_limit: int = 5_000,
    ) -> None:
        self._path = path
        self._persist = persist and path is not None
        self._flush_size = max(1, flush_size)
        self._pending: list[FactorSnapshot] = []
        self._recent: deque[FactorSnapshot] = deque(maxlen=max(1, memory_limit))
        self._recorded = 0
        self._dropped = 0
        if self._persist and self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path | None:
        """Backing JSONL file, if persistence is enabled."""
        return self._path if self._persist else None

    @property
    def recorded(self) -> int:
        """Fotos aceptadas desde el arranque."""
        return self._recorded

    def record(self, snapshot: FactorSnapshot) -> None:
        """Queue one snapshot, flushing when the batch is full.

        Args:
            snapshot: Foto de factores de una decisión.
        """
        self._recorded += 1
        self._recent.append(snapshot)
        if not self._persist:
            return
        self._pending.append(snapshot)
        if len(self._pending) >= self._flush_size:
            self.flush()

    def flush(self) -> int:
        """Write the pending batch to disk.

        Returns:
            Cuántas fotos se escribieron (0 si el disco falló: se registra, se
            descarta el lote y se sigue).
        """
        if not self._pending or self._path is None:
            return 0
        batch = self._pending
        self._pending = []
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                for snapshot in batch:
                    handle.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            self._dropped += len(batch)
            _LOG.error("No se pudieron persistir %d fotos de factores: %r", len(batch), exc)
            return 0
        return len(batch)

    def load(self) -> Iterator[FactorSnapshot]:
        """Stream every persisted snapshot (oldest first).

        Yields:
            Cada foto legible del fichero.
        """
        if self._path is None or not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield FactorSnapshot.from_dict(json.loads(line))
                    except (ValueError, KeyError, TypeError) as exc:
                        _LOG.warning("Foto de factores ilegible, se omite: %r", exc)
        except OSError as exc:
            _LOG.error("No se pudieron releer las fotos de factores: %r", exc)

    def index(self) -> dict[str, FactorSnapshot]:
        """Build the ``decision_id`` → snapshot map used by the join.

        Ante duplicados gana el **primero**: el store es append-only y la
        primera foto es la que se tomó junto a la decisión.

        Returns:
            Índice por ``decision_id``.
        """
        found: dict[str, FactorSnapshot] = {}
        for snapshot in self.load():
            found.setdefault(snapshot.decision_id, snapshot)
        # Lo que aún no ha llegado a disco también cuenta: si no, una operación
        # abierta y cerrada dentro del mismo lote se quedaría sin explicación
        # por un detalle de buffering, que es la clase de hueco imposible de
        # depurar. Y con el disco apagado, esto es la única fuente.
        for snapshot in self._recent:
            found.setdefault(snapshot.decision_id, snapshot)
        return found

    def status(self) -> dict[str, Any]:
        """Compact store status (dashboard/diagnóstico)."""
        return {
            "recorded": self._recorded,
            "pending": len(self._pending),
            "in_memory": len(self._recent),
            "dropped": self._dropped,
            "persist": self._persist,
            "path": str(self._path) if self._path else None,
        }

"""Histórico append-only de informes de edge.

Un informe que se reescribe deja de servir para lo único que importa aquí:
demostrar *cuándo* se supo que una estrategia se estaba deteriorando. Por eso
es append-only, igual que el Trade Journal y el store de resultados virtuales.
"""

import json
import logging
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.engine.edge_research.models import EdgeResearchReport

_LOG = logging.getLogger("app.engine.edge_research.history")


class EdgeReportHistory:
    """Append-only JSONL ledger of edge health reports.

    Args:
        path: Fichero JSONL de persistencia (``None`` desactiva el disco).
        persist: Si se escribe a disco.
        memory_limit: Cuántos informes se conservan en memoria para consulta
            inmediata del dashboard sin releer el fichero.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        persist: bool = True,
        memory_limit: int = 200,
    ) -> None:
        self._path = path
        self._persist = persist and path is not None
        self._recent: deque[EdgeResearchReport] = deque(maxlen=max(1, memory_limit))
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
        """Informes aceptados desde el arranque."""
        return self._recorded

    def record(self, report: EdgeResearchReport) -> None:
        """Store one report in memory and (best effort) on disk.

        Un fallo de disco nunca puede tumbar al motor de investigación: se
        registra, se cuenta como descartado y se sigue.

        Args:
            report: Informe del ciclo recién generado.
        """
        self._recorded += 1
        self._recent.append(report)
        if not self._persist or self._path is None:
            return
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            self._dropped += 1
            _LOG.error("No se pudo persistir el informe de edge: %r", exc)

    def recent(self, limit: int = 20) -> list[EdgeResearchReport]:
        """Latest reports held in memory (newest last).

        Args:
            limit: Cuántos devolver como máximo.

        Returns:
            Los informes más recientes, en orden cronológico.
        """
        if limit <= 0:
            return []
        return list(self._recent)[-limit:]

    def load(self) -> Iterator[EdgeResearchReport]:
        """Stream every persisted report (oldest first).

        Yields:
            Cada informe legible del fichero. Una línea corrupta se omite con
            aviso: no puede impedir leer el resto del histórico.
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
                        yield EdgeResearchReport.from_dict(json.loads(line))
                    except (ValueError, KeyError, TypeError) as exc:
                        _LOG.warning("Informe de edge ilegible, se omite: %r", exc)
        except OSError as exc:
            _LOG.error("No se pudo releer el histórico de edge: %r", exc)

    def series(self, strategy: str, limit: int = 200) -> list[dict[str, Any]]:
        """Historical evolution of one strategy's edge, for charting.

        Args:
            strategy: Estrategia a seguir.
            limit: Cuántos puntos devolver como máximo (los más recientes).

        Returns:
            Puntos ``{at, ...métricas}`` en orden cronológico.
        """
        points: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
        for report in self.load():
            found = report.by_strategy().get(strategy)
            if found is not None:
                points.append({"at": report.generated_at.isoformat(), **found.to_dict()})
        return list(points)

    def status(self) -> dict[str, Any]:
        """Compact store status (dashboard/diagnóstico)."""
        return {
            "recorded": self._recorded,
            "dropped": self._dropped,
            "in_memory": len(self._recent),
            "persist": self._persist,
            "path": str(self._path) if self._path else None,
        }

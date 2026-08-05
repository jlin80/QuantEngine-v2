"""Why Not Trade Engine (Bloque 14) — el registro de lo que no se operó.

Guarda cada rechazo con su desglose y responde la pregunta que hasta ahora sólo
se podía contestar leyendo logs a mano: **qué me está costando operaciones**. Un
filtro que bloquea el 60% de las oportunidades puede ser el que salva la cuenta o
el que la está estrangulando, y sin contarlas no hay forma de saber cuál de las
dos cosas es.

Append-only y con la misma disciplina que el resto de stores del proyecto: se
escribe por lotes, una línea corrupta no impide leer el resto, y un fallo de
disco nunca tumba a quien escribe.
"""

import json
import logging
from collections import Counter, deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.config.settings import QuantRejectionsSettings
from app.engine.rejections.models import RejectionRecord

_LOG = logging.getLogger("app.engine.rejections")


class RejectionStore:
    """Append-only ledger of rejected opportunities.

    Args:
        settings: Persistencia, lote y memoria.
    """

    def __init__(self, settings: QuantRejectionsSettings) -> None:
        self._settings = settings
        self._path: Path | None = settings.path if settings.persist else None
        self._pending: list[RejectionRecord] = []
        self._recent: deque[RejectionRecord] = deque(maxlen=max(1, settings.memory_limit))
        self._recorded = 0
        self._dropped = 0
        self._no_opportunity = 0
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, rejection: RejectionRecord) -> None:
        """Store one rejection.

        Args:
            rejection: El rechazo con su desglose completo.
        """
        if not self._settings.enabled:
            return
        self._recorded += 1
        self._recent.append(rejection)
        if self._path is None:
            return
        self._pending.append(rejection)
        if len(self._pending) >= self._settings.flush_size:
            self.flush()

    def note_no_opportunity(self) -> None:
        """Count an evaluation that never had an opportunity to reject.

        Sin senales activas no hay oportunidad que rechazar: no hay umbrales que
        medir ni filtros que hayan actuado. Guardar una fila por cada una de
        estas evaluaciones —dos simbolos por segundo son ~170.000 filas al dia—
        ahogaria los rechazos reales y dejaria el resumen dominado por un motivo
        que no es un motivo. Se cuenta, que es lo que hace falta para que el
        denominador sea honesto, y no se guarda.
        """
        if not self._settings.enabled:
            return
        self._no_opportunity += 1

    def flush(self) -> int:
        """Write the pending batch to disk.

        Returns:
            Cuántos rechazos se escribieron (0 si el disco falló).
        """
        if not self._pending or self._path is None:
            return 0
        batch = self._pending
        self._pending = []
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                for rejection in batch:
                    handle.write(json.dumps(rejection.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            self._dropped += len(batch)
            _LOG.error("No se pudieron persistir %d rechazos: %r", len(batch), exc)
            return 0
        return len(batch)

    def load(self) -> Iterator[RejectionRecord]:
        """Stream every persisted rejection (oldest first).

        Yields:
            Cada rechazo legible del fichero.
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
                        yield RejectionRecord.from_dict(json.loads(line))
                    except (ValueError, KeyError, TypeError) as exc:
                        _LOG.warning("Rechazo ilegible, se omite: %r", exc)
        except OSError as exc:
            _LOG.error("No se pudieron releer los rechazos: %r", exc)

    def recent(self, limit: int = 50, symbol: str | None = None) -> list[RejectionRecord]:
        """Latest rejections held in memory (newest first).

        Args:
            limit: Cuántos devolver como máximo.
            symbol: Filtro opcional por símbolo.

        Returns:
            Los rechazos más recientes.
        """
        items = list(self._recent)
        if symbol is not None:
            upper = symbol.upper()
            items = [r for r in items if r.symbol.upper() == upper]
        return list(reversed(items))[: max(0, limit)]

    def summary(self) -> dict[str, Any]:
        """Aggregate what is actually costing opportunities.

        Cuenta **dos cosas distintas** a propósito: cuántas veces cada puerta
        bloqueó, y cuántas veces fue la **única** que bloqueó. La segunda es la
        que importa para decidir si relajar un filtro: una puerta que siempre
        bloquea acompañada de otras tres no está costando nada — quitarla no
        habría dejado pasar ni una sola operación.

        Returns:
            Recuento por puerta, recuento en solitario y totales.
        """
        blocked = Counter[str]()
        alone = Counter[str]()
        for rejection in self._recent:
            names = rejection.blocked_by
            blocked.update(names)
            if len(names) == 1:
                alone.update(names)
        return {
            "sample": len(self._recent),
            "blocked_by": dict(blocked.most_common()),
            "sole_blocker": dict(alone.most_common()),
            # Evaluaciones que ni llegaron a ser una oportunidad. Se cuentan
            # aparte para que nadie las lea como "un filtro las bloqueo".
            "no_opportunity": self._no_opportunity,
        }

    def status(self) -> dict[str, Any]:
        """Compact store status (dashboard/diagnóstico)."""
        return {
            "enabled": self._settings.enabled,
            "recorded": self._recorded,
            "pending": len(self._pending),
            "in_memory": len(self._recent),
            "dropped": self._dropped,
            "no_opportunity": self._no_opportunity,
            "path": str(self._path) if self._path else None,
        }

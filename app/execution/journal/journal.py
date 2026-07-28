"""Trade Journal: registra absolutamente todo de cada operación cerrada.

Mantiene el historial en memoria y, opcionalmente, lo persiste en un fichero
JSON Lines (append-only). El journal nunca borra información: es la fuente de
verdad para el Performance Engine y para la auditoría.
"""

import json
import logging
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from app.execution.models import TradeRecord


class TradeJournal:
    """Append-only ledger of closed trades.

    Args:
        path: Fichero JSONL de persistencia (``None`` desactiva el disco).
        memory_limit: Máximo de operaciones retenidas en memoria.
        persist: Si se escribe a disco.
    """

    def __init__(
        self, path: Path | None = None, *, memory_limit: int = 10_000, persist: bool = True
    ) -> None:
        self._path = path
        self._persist = persist and path is not None
        self._trades: deque[TradeRecord] = deque(maxlen=memory_limit)
        self._log = logging.getLogger("app.execution.journal")
        if self._persist and self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, trade: TradeRecord) -> None:
        """Append a trade to the journal (memory + optional disk).

        Args:
            trade: Operación cerrada a registrar.
        """
        self._trades.append(trade)
        if self._persist and self._path is not None:
            try:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")
            except OSError as exc:
                # El disco nunca debe tumbar la operativa: se registra y sigue.
                self._log.error("No se pudo persistir la operación %s: %r", trade.trade_id, exc)

    @property
    def count(self) -> int:
        """Number of trades held in memory."""
        return len(self._trades)

    @property
    def path(self) -> Path | None:
        """Backing JSONL file, if persistence is enabled."""
        return self._path if self._persist else None

    def restore(self, trades: Iterable[TradeRecord]) -> int:
        """Reload previously journalled trades into memory (Fase 9).

        El journal escribía a disco desde la Fase 5 pero nunca releía: al
        reiniciar, el histórico en memoria arrancaba vacío mientras el fichero
        seguía creciendo, y el Performance Engine calculaba sobre la nada.

        No re-escribe a disco: estas operaciones ya están en el fichero.

        Args:
            trades: Operaciones recuperadas, de más antigua a más reciente.

        Returns:
            Cuántas se cargaron en memoria.
        """
        loaded = 0
        for trade in trades:
            self._trades.append(trade)
            loaded += 1
        if loaded:
            self._log.info("Journal restaurado: %d operaciones en memoria", loaded)
        return loaded

    def load_from_disk(self) -> int:
        """Reload this journal's own JSONL file into memory.

        El journal escribía a disco desde la Fase 5 pero sólo se releía a través
        del servicio de recuperación de la Fase 9 — que está apagado por
        defecto. Resultado: en cada reinicio el histórico de Operations y las
        métricas del Performance Engine arrancaban vacíos mientras el fichero
        seguía creciendo. Esto lo hace autónomo de la capa de producción.

        Idempotente: si ya hay operaciones en memoria no vuelve a cargar.

        Returns:
            Cuántas operaciones se cargaron.
        """
        if self._trades or self._path is None or not self._path.exists():
            return 0
        trades: list[TradeRecord] = []
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        trades.append(TradeRecord.from_dict(json.loads(line)))
                    except (ValueError, KeyError, TypeError) as exc:
                        # Una línea corrupta no puede impedir cargar el resto.
                        self._log.warning("Línea del journal ilegible, se omite: %r", exc)
        except OSError as exc:
            self._log.error("No se pudo releer el journal: %r", exc)
            return 0
        return self.restore(trades)

    def all(self) -> list[TradeRecord]:
        """Every trade held in memory (oldest first)."""
        return list(self._trades)

    def recent(self, limit: int = 50) -> list[TradeRecord]:
        """Most recent trades (newest last)."""
        return list(self._trades)[-limit:]

    def for_symbol(self, symbol: str) -> list[TradeRecord]:
        """Trades for a single symbol."""
        symbol = symbol.upper()
        return [t for t in self._trades if t.symbol == symbol]

    def status(self) -> dict[str, object]:
        """Compact journal status."""
        return {
            "count": self.count,
            "persist": self._persist,
            "path": str(self._path) if self._path else None,
        }

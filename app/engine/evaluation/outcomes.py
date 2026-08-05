"""Resultado virtual de cada señal, indexado por ``signal_id``.

El evaluador continuo (Fase 4) resolvía cada operación virtual y **descartaba
el resultado individual**: sólo sobrevivía el agregado por estrategia
(:class:`StrategyPerformance`). Eso basta para ponderar una estrategia, pero no
para responder la pregunta del Bloque 8 — *esta operación concreta, ¿venía de
una señal con edge, o de una señal mala que la ejecución no llegó a poner a
prueba?*.

Este store conserva la fila. Es append-only, como el Trade Journal, por el
mismo motivo: un resultado que se reescribe deja de ser evidencia. El agregado
por estrategia no cambia — esto añade una salida, no sustituye ninguna.
"""

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("app.engine.evaluation.outcomes")


@dataclass(frozen=True, kw_only=True, slots=True)
class VirtualOutcome:
    """Resolución de la operación virtual abierta por una señal.

    Attributes:
        signal_id: Señal de origen. Es la clave del join con el Trade Journal.
        strategy: Estrategia que emitió la señal.
        symbol: Activo.
        direction: Dirección de la señal (``long``/``short``).
        entry: Precio de entrada hipotético.
        stop: Stop de la señal (base de la R).
        target: Objetivo, si la señal lo declaraba.
        r_multiple: Resultado en múltiplos de R.
        outcome: ``win`` / ``loss`` / ``timeout``.
        false_signal: Si se movió en contra antes de avanzar a favor.
        opened_at: Momento de la señal.
        closed_at: Momento de la resolución.
        confidence: Confianza que la estrategia declaró al emitir la señal.
            Es lo que permite medir la deriva de confianza (Bloque 1): sin ella
            no se puede distinguir una estrategia que empeora de una que además
            se cree cada vez más segura. ``None`` en las filas escritas antes
            del Bloque 1, que por eso quedan fuera de esa métrica en vez de
            entrar con un valor inventado.
    """

    signal_id: str
    strategy: str
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float | None
    r_multiple: float
    outcome: str
    false_signal: bool
    opened_at: datetime
    closed_at: datetime
    confidence: float | None = None

    @property
    def holding_seconds(self) -> float:
        """Duración de la operación virtual."""
        return (self.closed_at - self.opened_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "signal_id": self.signal_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "r_multiple": self.r_multiple,
            "outcome": self.outcome,
            "false_signal": self.false_signal,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "holding_seconds": self.holding_seconds,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VirtualOutcome":
        """Rebuild an outcome from one JSONL line.

        Args:
            data: Entrada del fichero ya parseada.

        Returns:
            El resultado reconstruido.

        Raises:
            KeyError: Si falta un campo obligatorio.
            ValueError: Si algún campo no se puede convertir.
        """
        target = data.get("target")
        confidence = data.get("confidence")
        return cls(
            signal_id=str(data["signal_id"]),
            strategy=str(data.get("strategy", "")),
            symbol=str(data.get("symbol", "")),
            direction=str(data.get("direction", "")),
            entry=float(data["entry"]),
            stop=float(data["stop"]),
            target=None if target is None else float(target),
            r_multiple=float(data["r_multiple"]),
            outcome=str(data["outcome"]),
            false_signal=bool(data.get("false_signal", False)),
            opened_at=datetime.fromisoformat(str(data["opened_at"])),
            closed_at=datetime.fromisoformat(str(data["closed_at"])),
            confidence=None if confidence is None else float(confidence),
        )


class VirtualOutcomeStore:
    """Append-only ledger of resolved virtual trades, keyed by ``signal_id``.

    Se escribe por lotes: el evaluador resuelve en tandas dentro de su propio
    bucle, y un ``open()`` por señal resuelta sería I/O gratuito en el camino
    del evaluador.

    Args:
        path: Fichero JSONL de persistencia (``None`` desactiva el disco).
        persist: Si se escribe a disco.
        flush_size: Resoluciones acumuladas antes de volcar.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        persist: bool = True,
        flush_size: int = 50,
    ) -> None:
        self._path = path
        self._persist = persist and path is not None
        self._flush_size = max(1, flush_size)
        self._pending: list[VirtualOutcome] = []
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
        """Resoluciones aceptadas por el store desde el arranque."""
        return self._recorded

    def record(self, outcome: VirtualOutcome) -> None:
        """Queue one resolution, flushing when the batch is full.

        Args:
            outcome: Resolución de una operación virtual.
        """
        self._recorded += 1
        if not self._persist:
            return
        self._pending.append(outcome)
        if len(self._pending) >= self._flush_size:
            self.flush()

    def flush(self) -> int:
        """Write the pending batch to disk.

        Un fallo de disco nunca puede tumbar al evaluador: se registra, se
        descarta el lote y se sigue (misma regla que el Trade Journal).

        Returns:
            Cuántas resoluciones se escribieron.
        """
        if not self._pending or self._path is None:
            return 0
        batch = self._pending
        self._pending = []
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                for outcome in batch:
                    handle.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            self._dropped += len(batch)
            _LOG.error("No se pudieron persistir %d resultados virtuales: %r", len(batch), exc)
            return 0
        return len(batch)

    def load(self) -> Iterator[VirtualOutcome]:
        """Stream every persisted outcome (oldest first).

        Es un iterador a propósito: el fichero crece sin borrado, y el join del
        ML sólo necesita construir su índice, no una lista entera en memoria.

        Yields:
            Cada resolución legible del fichero.
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
                        yield VirtualOutcome.from_dict(json.loads(line))
                    except (ValueError, KeyError, TypeError) as exc:
                        # Una línea corrupta no puede impedir leer el resto.
                        _LOG.warning("Resultado virtual ilegible, se omite: %r", exc)
        except OSError as exc:
            _LOG.error("No se pudieron releer los resultados virtuales: %r", exc)

    def index(self) -> dict[str, VirtualOutcome]:
        """Build the ``signal_id`` → outcome map used by the ML join.

        Ante duplicados gana el **primero**: el store es append-only y la
        primera resolución es la que el evaluador consideró válida.

        Returns:
            Índice por ``signal_id``.
        """
        found: dict[str, VirtualOutcome] = {}
        for outcome in self.load():
            found.setdefault(outcome.signal_id, outcome)
        return found

    def status(self) -> dict[str, Any]:
        """Compact store status (dashboard/diagnóstico)."""
        return {
            "recorded": self._recorded,
            "pending": len(self._pending),
            "dropped": self._dropped,
            "persist": self._persist,
            "path": str(self._path) if self._path else None,
        }


def index_outcomes(outcomes: Iterable[VirtualOutcome]) -> dict[str, VirtualOutcome]:
    """Index any iterable of outcomes by ``signal_id`` (first wins).

    Args:
        outcomes: Resoluciones a indexar.

    Returns:
        Índice por ``signal_id``.
    """
    found: dict[str, VirtualOutcome] = {}
    for outcome in outcomes:
        found.setdefault(outcome.signal_id, outcome)
    return found

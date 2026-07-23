"""Persistencia batched del historial (mismo patrón que el market writer)."""

import asyncio
import contextlib
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config.settings import QuantHistorySettings
from app.core.exceptions import DatabaseError
from app.core.lifecycle import Service
from app.database.engine import DatabaseManager
from app.engine.models import Decision, SignalRecord
from app.engine.state_manager.models import DecisionRow, SignalRow
from app.utils.time import utc_now


class HistoryWriter(Service):
    """Batched, fault-tolerant persistence for signals and decisions.

    Args:
        db: Gestor de base de datos.
        settings: Batching y spill.
    """

    def __init__(self, db: DatabaseManager, settings: QuantHistorySettings) -> None:
        super().__init__("history_writer")
        self._db = db
        self._settings = settings
        self._signals: deque[dict[str, Any]] = deque(maxlen=settings.memory_limit)
        self._decisions: deque[dict[str, Any]] = deque(maxlen=settings.memory_limit)
        self._task: asyncio.Task[None] | None = None
        self._db_available = True
        self._rows_written = 0
        self._rows_spilled = 0
        self._log = logging.getLogger("app.engine.history_writer")

    def add_signal(self, record: SignalRecord) -> None:
        """Buffer a resolved signal record."""
        if not self._settings.persist:
            return
        signal = record.signal
        self._signals.append(
            {
                "signal_id": signal.signal_id,
                "strategy": signal.strategy_name,
                "symbol": signal.symbol,
                "direction": signal.direction.value,
                "score": signal.score,
                "confidence": signal.confidence,
                "status": record.status.value,
                "status_reasons": json.dumps(list(record.status_reasons)),
                "payload": json.dumps(signal.to_dict(), default=str),
                "created_at": record.recorded_at,
                "resolved_at": record.resolved_at,
            }
        )

    def add_decision(self, decision: Decision) -> None:
        """Buffer a decision."""
        if not self._settings.persist:
            return
        self._decisions.append(
            {
                "decision_id": decision.decision_id,
                "symbol": decision.symbol,
                "action": decision.action.value,
                "accepted": decision.accepted,
                "score": decision.score,
                "confidence": decision.confidence,
                "agreement": decision.agreement,
                "regime": decision.regime,
                "payload": json.dumps(decision.to_dict(), default=str),
                "created_at": decision.timestamp,
            }
        )

    async def _on_start(self) -> None:
        if not self._settings.persist:
            return
        self._task = asyncio.create_task(self._loop(), name="history-writer-flush")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.flush()

    async def _loop(self) -> None:
        """Flush on a fixed interval."""
        while True:
            await asyncio.sleep(self._settings.flush_interval_seconds)
            await self.flush()

    async def flush(self) -> None:
        """Drain both buffers to the database (spill on failure)."""
        await self._flush_batch(self._signals, SignalRow, "signals", key="signal_id")
        await self._flush_batch(self._decisions, DecisionRow, "decisions", key="decision_id")

    async def _flush_batch(
        self,
        buffer: deque[dict[str, Any]],
        model: type[SignalRow] | type[DecisionRow],
        label: str,
        *,
        key: str,
    ) -> None:
        """Upsert up to batch_size rows; spill to JSONL if the DB is down."""
        if not buffer:
            return
        batch: list[dict[str, Any]] = []
        while buffer and len(batch) < self._settings.batch_size:
            batch.append(buffer.popleft())
        try:
            async with self._db.session() as session:
                statement = pg_insert(model).values(batch)
                statement = statement.on_conflict_do_nothing(index_elements=[key])
                await session.execute(statement)
            self._rows_written += len(batch)
            if not self._db_available:
                self._db_available = True
                self._log.info("Database is back — resuming %s persistence", label)
        except (DatabaseError, OSError) as exc:
            if self._db_available:
                self._db_available = False
                self._log.warning("Database unavailable for %s — spilling: %s", label, exc)
            self._spill(batch, label)

    def _spill(self, batch: list[dict[str, Any]], label: str) -> None:
        """Append the failed batch to a JSONL spill file (never raises)."""
        try:
            spill_dir = Path(self._settings.spill_dir)
            spill_dir.mkdir(parents=True, exist_ok=True)
            path = spill_dir / f"engine-{label}-{utc_now():%Y%m%d}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                for row in batch:
                    handle.write(json.dumps(row, default=str) + "\n")
            self._rows_spilled += len(batch)
        except OSError:
            self._log.exception("Spill to disk failed — %d rows lost", len(batch))

    async def healthcheck(self) -> bool:
        """Healthy while running (DB caída = degradado pero operable)."""
        return self.is_running

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot."""
        return {
            "persist": self._settings.persist,
            "db_available": self._db_available,
            "pending_signals": len(self._signals),
            "pending_decisions": len(self._decisions),
            "rows_written": self._rows_written,
            "rows_spilled": self._rows_spilled,
        }

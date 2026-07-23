"""Escritor de datos de mercado: batching, tolerancia a fallos y spill.

Bufferiza en memoria (``add_*`` nunca bloquea al pipeline), hace flush por
lotes en intervalos regulares y, si la base de datos no está disponible,
derrama los lotes a JSONL en disco para no perder eventos.
"""

import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config.settings import MarketStorageSettings
from app.core.exceptions import DatabaseError
from app.core.lifecycle import Service
from app.database.engine import DatabaseManager
from app.market.models import Candle, Trade
from app.market.storage.models import CandleRow, TickRow
from app.utils.time import utc_now


class MarketDataWriter(Service):
    """Batched, fault-tolerant persistence sink for ticks and candles.

    Args:
        db: Gestor de base de datos (conexión perezosa).
        settings: Parámetros de batching y spill.
    """

    def __init__(self, db: DatabaseManager, settings: MarketStorageSettings) -> None:
        super().__init__("market_writer")
        self._db = db
        self._settings = settings
        self._ticks: deque[dict[str, Any]] = deque(maxlen=settings.buffer_limit)
        self._candles: deque[dict[str, Any]] = deque(maxlen=settings.buffer_limit)
        self._flush_task: asyncio.Task[None] | None = None
        self._db_available = True
        self._rows_written = 0
        self._rows_spilled = 0
        self._log = logging.getLogger("app.market.writer")

    # ------------------------------------------------------------------
    # Entrada (no bloqueante, la llama el pipeline)
    # ------------------------------------------------------------------

    def add_trade(self, trade: Trade) -> None:
        """Buffer one trade for persistence."""
        if not (self._settings.enabled and self._settings.store_ticks):
            return
        self._ticks.append(
            {
                "symbol": trade.symbol,
                "provider": trade.provider,
                "trade_id": trade.trade_id,
                "price": trade.price,
                "size": trade.size,
                "side": trade.side.value,
                "exchange_ts": trade.exchange_ts,
                "local_ts": trade.local_ts,
                "latency_ms": trade.latency_ms,
            }
        )

    def add_candles(self, candles: Sequence[Candle]) -> None:
        """Buffer closed candles for persistence."""
        if not (self._settings.enabled and self._settings.store_candles):
            return
        for candle in candles:
            if not candle.closed:
                continue
            self._candles.append(
                {
                    "symbol": candle.symbol,
                    "provider": candle.provider,
                    "timeframe": candle.timeframe.value,
                    "start": candle.start,
                    "end": candle.end,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "buy_volume": candle.buy_volume,
                    "sell_volume": candle.sell_volume,
                    "vwap": candle.vwap,
                    "trades": candle.trades,
                    "closed": candle.closed,
                    "source": candle.source,
                }
            )

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _on_start(self) -> None:
        if not self._settings.enabled:
            return
        self._flush_task = asyncio.create_task(self._flush_loop(), name="market-writer-flush")

    async def _on_stop(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None
        await self.flush()  # drenar lo pendiente antes de morir

    async def _flush_loop(self) -> None:
        """Flush buffers on a fixed interval."""
        while True:
            await asyncio.sleep(self._settings.flush_interval_seconds)
            await self.flush()

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Drain buffers to the database (spill to disk on failure)."""
        await self._flush_batch(self._ticks, TickRow, "ticks")
        await self._flush_batch(self._candles, CandleRow, "candles", upsert=True)

    async def _flush_batch(
        self,
        buffer: deque[dict[str, Any]],
        model: type[TickRow] | type[CandleRow],
        label: str,
        *,
        upsert: bool = False,
    ) -> None:
        """Insert up to ``batch_size`` rows; on DB failure spill to JSONL."""
        if not buffer:
            return
        batch: list[dict[str, Any]] = []
        while buffer and len(batch) < self._settings.batch_size:
            batch.append(buffer.popleft())
        try:
            async with self._db.session() as session:
                if upsert:
                    statement = pg_insert(model).values(batch)
                    statement = statement.on_conflict_do_nothing(
                        index_elements=["symbol", "provider", "timeframe", "start"]
                    )
                    await session.execute(statement)
                else:
                    await session.execute(insert(model), batch)
            self._rows_written += len(batch)
            if not self._db_available:
                self._db_available = True
                self._log.info("Database is back — resuming %s persistence", label)
        except (DatabaseError, OSError) as exc:
            # OSError cubre fallos de conexión crudos (asyncpg) que SQLAlchemy
            # no envuelve como SQLAlchemyError.
            if self._db_available:
                self._db_available = False
                self._log.warning("Database unavailable for %s — spilling to disk: %s", label, exc)
            self._spill(batch, label)

    def _spill(self, batch: list[dict[str, Any]], label: str) -> None:
        """Append the failed batch to a JSONL spill file (never raises)."""
        try:
            spill_dir = Path(self._settings.spill_dir)
            spill_dir.mkdir(parents=True, exist_ok=True)
            path = spill_dir / f"{label}-{utc_now():%Y%m%d}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                for row in batch:
                    handle.write(json.dumps(row, default=str) + "\n")
            self._rows_spilled += len(batch)
        except OSError:
            self._log.exception("Spill to disk failed — %d rows lost", len(batch))

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------

    async def healthcheck(self) -> bool:
        """Healthy while running (DB caída = degradado pero operable)."""
        return self.is_running

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot."""
        return {
            "enabled": self._settings.enabled,
            "db_available": self._db_available,
            "pending_ticks": len(self._ticks),
            "pending_candles": len(self._candles),
            "rows_written": self._rows_written,
            "rows_spilled": self._rows_spilled,
        }

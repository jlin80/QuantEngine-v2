"""Repositorio de lectura de datos de mercado persistidos."""

from datetime import datetime

from sqlalchemy import select

from app.database.engine import DatabaseManager
from app.database.repository import SQLAlchemyRepository
from app.market.models import Timeframe
from app.market.storage.models import CandleRow, TickRow


class MarketDataRepository(SQLAlchemyRepository[CandleRow]):
    """Consultas sobre las tablas de mercado (para fases futuras y backfill).

    Args:
        db: Gestor de base de datos.
    """

    def __init__(self, db: DatabaseManager) -> None:
        super().__init__(db, CandleRow)
        self._db_manager = db

    async def candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[CandleRow]:
        """Closed candles for a series, ascending by start.

        Args:
            symbol: Símbolo interno.
            timeframe: Timeframe de la serie.
            since: Solo velas con ``start`` posterior (opcional).
            limit: Máximo de filas.

        Returns:
            Filas ORM ascendentes por ``start``.
        """
        statement = (
            select(CandleRow)
            .where(CandleRow.symbol == symbol.upper())
            .where(CandleRow.timeframe == timeframe.value)
            .order_by(CandleRow.start.desc())
            .limit(limit)
        )
        if since is not None:
            statement = statement.where(CandleRow.start >= since)
        async with self._db_manager.session() as session:
            rows = (await session.execute(statement)).scalars().all()
        return list(reversed(rows))

    async def recent_ticks(self, symbol: str, *, limit: int = 1000) -> list[TickRow]:
        """Most recent persisted ticks, ascending by exchange timestamp."""
        statement = (
            select(TickRow)
            .where(TickRow.symbol == symbol.upper())
            .order_by(TickRow.exchange_ts.desc())
            .limit(limit)
        )
        async with self._db_manager.session() as session:
            rows = (await session.execute(statement)).scalars().all()
        return list(reversed(rows))

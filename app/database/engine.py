"""Gestión del engine async y las sesiones (pool de conexiones incluido)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import DatabaseSettings
from app.core.exceptions import DatabaseError


class DatabaseManager:
    """Owner of the async engine and session factory.

    La creación del engine es perezosa: no se abre conexión alguna hasta el
    primer uso, de modo que el sistema puede arrancar sin base de datos.

    Args:
        settings: Database section of the configuration.
    """

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._log = logging.getLogger("app.database")

    @property
    def engine(self) -> AsyncEngine:
        """The lazily-created async engine."""
        if self._engine is None:
            self._engine = create_async_engine(
                self._settings.dsn,
                echo=self._settings.echo,
                pool_size=self._settings.pool_size,
                max_overflow=self._settings.max_overflow,
                pool_pre_ping=self._settings.pool_pre_ping,
            )
            self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Provide a transactional session scope.

        Yields:
            An :class:`AsyncSession` committed on success, rolled back on error.

        Raises:
            DatabaseError: On any SQLAlchemy failure inside the scope.
        """
        _ = self.engine  # asegura la factory
        assert self._session_factory is not None
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise DatabaseError(
                    "Database session failed", context={"error": repr(exc)}
                ) from exc

    async def ping(self) -> bool:
        """Return whether the database answers a trivial query."""
        from sqlalchemy import text

        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            self._log.warning("Database ping failed: %r", exc)
            return False
        return True

    async def dispose(self) -> None:
        """Dispose the engine and its connection pool."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

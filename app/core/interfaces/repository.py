"""Contrato genérico de repositorios (patrón Repository).

Fase 1: sin tablas ni modelos — solo el contrato que las fases siguientes
implementarán con SQLAlchemy.
"""

from typing import Protocol, TypeVar

TEntity = TypeVar("TEntity")
TId = TypeVar("TId", contravariant=True)


class Repository(Protocol[TEntity, TId]):
    """Persistence-agnostic repository contract."""

    async def get(self, entity_id: TId) -> TEntity | None:
        """Return the entity with ``entity_id`` or ``None``."""
        ...

    async def add(self, entity: TEntity) -> TEntity:
        """Persist a new entity and return it."""
        ...

    async def update(self, entity: TEntity) -> TEntity:
        """Persist changes to an existing entity."""
        ...

    async def delete(self, entity_id: TId) -> None:
        """Remove the entity with ``entity_id``."""
        ...

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> list[TEntity]:
        """Return a page of entities."""
        ...

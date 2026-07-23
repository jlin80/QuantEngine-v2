"""Capa de persistencia: SQLAlchemy async + Alembic (sin tablas en Fase 1)."""

from app.database.base import Base
from app.database.engine import DatabaseManager

__all__ = ["Base", "DatabaseManager"]

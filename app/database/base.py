"""Base declarativa y convenciones de nombres para toda la persistencia.

Sin tablas en Fase 1: los modelos de fases futuras heredarán de ``Base`` y
las migraciones Alembic detectarán su metadata automáticamente.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Convención estable de nombres → migraciones deterministas.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

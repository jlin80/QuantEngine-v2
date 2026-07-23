"""Esqueleto de repositorio SQLAlchemy (patrón Repository).

Fase 1 no define modelos; este módulo fija la forma que tendrán los
repositorios concretos para que las fases siguientes solo aporten entidades.
"""

from app.database.engine import DatabaseManager


class SQLAlchemyRepository[TModel]:
    """Base for concrete repositories bound to one ORM model.

    Args:
        db: Database manager providing session scopes.
        model: ORM model class handled by this repository.
    """

    def __init__(self, db: DatabaseManager, model: type[TModel]) -> None:
        self._db = db
        self._model = model

    @property
    def model(self) -> type[TModel]:
        """ORM model class handled by this repository."""
        return self._model

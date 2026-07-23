"""Definición profesional de una feature (metadatos completos, Fase 7).

Cada feature del ML deja de ser un número anónimo: se cataloga con nombre,
versión, descripción, fuente, tipo, fecha, validez y dependencias. Esto permite
auditar de dónde sale cada variable y no recalcular lo que sigue vigente.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.utils.time import utc_now


class FeatureType(StrEnum):
    """Tipo de dato de una feature."""

    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BINARY = "binary"
    CYCLICAL = "cyclical"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """Full metadata for a single feature.

    Attributes:
        name: Nombre único de la feature.
        version: Versión del cálculo (``1.0``, ``1.1``...).
        description: Qué representa y cómo se interpreta.
        source: Origen del dato (``trade_journal``, ``market``, ``engine``...).
        dtype: Tipo de la feature.
        created_at: Fecha de alta (ISO-8601 UTC).
        validity_seconds: Cuánto tiempo se considera vigente un valor cacheado.
        dependencies: Otras features/insumos de los que depende.
    """

    name: str
    version: str
    description: str
    source: str
    dtype: FeatureType = FeatureType.NUMERIC
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    validity_seconds: float = 1.0
    dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "source": self.source,
            "dtype": self.dtype.value,
            "created_at": self.created_at,
            "validity_seconds": self.validity_seconds,
            "dependencies": list(self.dependencies),
        }

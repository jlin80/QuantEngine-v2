"""Feature Store del laboratorio (Fase 6).

Almacena las variables (features) calculadas durante el backtesting para no
recalcularlas: se guardan por ``(dataset, nombre, versión)`` y se reutilizan.
Versionar las features permite reproducir un experimento con exactamente las
mismas entradas aunque la fórmula de una feature cambie más adelante.
"""

from collections.abc import Callable, Sequence
from typing import Any


class BacktestFeatureStore:
    """Versioned cache of computed feature series.

    Reutiliza el valor guardado si ya existe para la misma clave; si no, lo
    calcula una única vez y lo memoriza.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], Sequence[float]] = {}

    @staticmethod
    def _key(dataset: str, name: str, version: str) -> tuple[str, str, str]:
        """Build the storage key for a feature."""
        return (dataset, name, version)

    def put(self, dataset: str, name: str, version: str, values: Sequence[float]) -> None:
        """Store a computed feature series under a versioned key."""
        self._store[self._key(dataset, name, version)] = list(values)

    def get(self, dataset: str, name: str, version: str) -> Sequence[float] | None:
        """Return a stored feature series (or ``None`` if absent)."""
        return self._store.get(self._key(dataset, name, version))

    def get_or_compute(
        self,
        dataset: str,
        name: str,
        version: str,
        compute: Callable[[], Sequence[float]],
    ) -> Sequence[float]:
        """Return a stored feature or compute, store and return it once.

        Args:
            dataset: Identificador del dataset.
            name: Nombre de la feature.
            version: Versión de la fórmula de la feature.
            compute: Función que calcula la serie si no está cacheada.

        Returns:
            La serie de la feature (nunca recalculada dos veces).
        """
        cached = self.get(dataset, name, version)
        if cached is not None:
            return cached
        values = list(compute())
        self.put(dataset, name, version, values)
        return values

    def keys(self) -> list[tuple[str, str, str]]:
        """Every stored feature key."""
        return list(self._store)

    def status(self) -> dict[str, Any]:
        """Compact status for diagnostics."""
        datasets = {key[0] for key in self._store}
        return {"features": len(self._store), "datasets": sorted(datasets)}

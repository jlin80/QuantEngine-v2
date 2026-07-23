"""Feature Store profesional del ML: catálogo versionado + cómputo único.

Distinto del Feature Store de mercado (``app.engine.feature_store``, que cachea
indicadores en vivo): éste es el **catálogo** de las features del ML, con sus
metadatos completos y versionado. Nunca sobrescribe una versión; publica una
nueva. Y evita recalcular: un valor cacheado se reutiliza mientras siga vigente
según la ``validity_seconds`` de su definición.
"""

import time
from collections.abc import Callable
from typing import Any

from app.core.exceptions import MLError
from app.ml.feature_store.definitions import FeatureDefinition, FeatureType
from app.ml.features.engineer import FeatureEngineer


class FeatureStore:
    """Versioned catalogue of ML features with compute-once caching."""

    def __init__(self) -> None:
        # name -> {version -> definition}
        self._definitions: dict[str, dict[str, FeatureDefinition]] = {}
        self._latest: dict[str, str] = {}
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}
        self._hits = 0
        self._misses = 0

    def register(self, definition: FeatureDefinition, *, replace: bool = False) -> None:
        """Register a feature definition (never overwrites a version silently).

        Args:
            definition: Definición completa de la feature.
            replace: Permite re-publicar exactamente la misma versión.

        Raises:
            MLError: Si la versión ya existe y ``replace`` es ``False``.
        """
        versions = self._definitions.setdefault(definition.name, {})
        if definition.version in versions and not replace:
            raise MLError(
                f"La feature '{definition.name}' v{definition.version} ya está registrada; "
                "publica una versión nueva en lugar de sobrescribir.",
                context={"feature": definition.name, "version": definition.version},
            )
        versions[definition.version] = definition
        self._latest[definition.name] = _max_version(versions)

    def register_engineer(
        self, engineer: FeatureEngineer, *, source: str = "trade_journal"
    ) -> None:
        """Register every feature produced by a :class:`FeatureEngineer`."""
        for name in engineer.feature_names():
            self.register(
                FeatureDefinition(
                    name=name,
                    version=engineer.version,
                    description=_describe(name),
                    source=source,
                    dtype=_dtype_for(name),
                    validity_seconds=1.0,
                    dependencies=(),
                ),
                replace=True,
            )

    def get(self, name: str, version: str | None = None) -> FeatureDefinition:
        """Return a feature definition (latest version by default).

        Raises:
            MLError: Si la feature (o la versión) no existe.
        """
        versions = self._definitions.get(name)
        if not versions:
            raise MLError(f"Feature desconocida: '{name}'", context={"feature": name})
        key = version or self._latest[name]
        if key not in versions:
            raise MLError(f"Versión desconocida de '{name}': v{key}", context={"feature": name})
        return versions[key]

    def definitions(self) -> list[FeatureDefinition]:
        """Every latest-version definition, sorted by name."""
        return [self._definitions[name][self._latest[name]] for name in sorted(self._definitions)]

    def versions(self, name: str) -> list[str]:
        """All registered versions of a feature (ascending)."""
        return sorted(self._definitions.get(name, {}), key=_version_key)

    def get_or_compute(
        self, name: str, key: str, compute: Callable[[], Any], *, version: str | None = None
    ) -> Any:
        """Return a cached feature value or compute it once within its validity.

        Args:
            name: Feature registrada.
            key: Clave del valor concreto (p. ej. el símbolo).
            compute: Cómputo perezoso del valor si no hay cache vigente.
            version: Versión concreta (por defecto la última).

        Returns:
            El valor (cacheado mientras siga vigente).
        """
        definition = self.get(name, version)
        cache_key = (f"{name}@{definition.version}", key)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and now - cached[0] < definition.validity_seconds:
            self._hits += 1
            return cached[1]
        self._misses += 1
        value = compute()
        self._cache[cache_key] = (now, value)
        return value

    def invalidate(self, name: str | None = None) -> None:
        """Drop cached values (for one feature or all)."""
        if name is None:
            self._cache.clear()
            return
        for cache_key in [k for k in self._cache if k[0].split("@", 1)[0] == name]:
            del self._cache[cache_key]

    @property
    def stats(self) -> dict[str, int]:
        """Catalogue and cache counters."""
        return {
            "features": len(self._definitions),
            "cached": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
        }

    def catalog(self) -> list[dict[str, Any]]:
        """JSON-safe catalogue for the dashboard."""
        return [definition.to_dict() for definition in self.definitions()]


def _version_key(version: str) -> tuple[int, ...]:
    """Sort key for a dotted version string."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def _max_version(versions: dict[str, FeatureDefinition]) -> str:
    """Highest version string in a version map."""
    return max(versions, key=_version_key)


def _describe(name: str) -> str:
    """Human description for a known engineered feature name."""
    if name.startswith("sess_"):
        return f"Pertenencia a la sesión de mercado {name[5:]} (0/1)."
    if name.startswith("regime_"):
        return f"Régimen de mercado one-hot: {name[7:]}."
    if name.startswith("vol_"):
        return f"Estado de volatilidad one-hot: {name[4:]}."
    known = {
        "hour_norm": "Hora UTC de entrada, normalizada a [0,1].",
        "score": "Score de la decisión (0-100) normalizado.",
        "confidence": "Confianza de la decisión (0-1).",
        "atr_pct": "ATR como porcentaje del precio de entrada.",
        "spread_bps": "Spread medio en puntos básicos.",
        "planned_rr": "Relación beneficio/riesgo planificada.",
        "n_confirmations": "Número de confirmaciones de la señal (normalizado).",
        "side_long": "Dirección: 1 largo, 0 corto.",
    }
    return known.get(name, name)


def _dtype_for(name: str) -> FeatureType:
    """Infer the feature type from a known engineered name."""
    if name.startswith(("sess_", "regime_", "vol_", "side_")):
        return FeatureType.BINARY
    if name == "hour_norm":
        return FeatureType.CYCLICAL
    return FeatureType.NUMERIC

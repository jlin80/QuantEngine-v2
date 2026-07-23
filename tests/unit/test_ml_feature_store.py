"""Feature Store profesional del ML: catálogo versionado y cómputo único."""

import pytest
from app.core.exceptions import MLError
from app.ml.feature_store import FeatureDefinition, FeatureStore, FeatureType
from app.ml.features import FeatureEngineer


def _definition(
    name: str = "vwap_dist",
    version: str = "1.0",
    *,
    description: str = "Distancia al VWAP en ATRs.",
    source: str = "orderflow",
    dtype: FeatureType = FeatureType.NUMERIC,
    validity_seconds: float = 60.0,
    dependencies: tuple[str, ...] = (),
) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        version=version,
        description=description,
        source=source,
        dtype=dtype,
        validity_seconds=validity_seconds,
        dependencies=dependencies,
    )


def test_engineer_features_are_catalogued_with_metadata():
    store = FeatureStore()
    engineer = FeatureEngineer()
    store.register_engineer(engineer)

    catalog = store.catalog()
    names = {entry["name"] for entry in catalog}
    assert names == set(engineer.feature_names())
    # Cada feature lleva metadatos completos (nombre, versión, fuente, tipo).
    sample = store.get("score")
    assert sample.version == engineer.version
    assert sample.source == "trade_journal"
    assert sample.description  # descripción no vacía


def test_register_never_overwrites_a_version():
    store = FeatureStore()
    store.register(_definition(version="1.0"))
    with pytest.raises(MLError):
        store.register(_definition(version="1.0"))
    # Publicar una versión nueva sí está permitido.
    store.register(_definition(version="2.0", description="Nueva definición."))
    assert store.versions("vwap_dist") == ["1.0", "2.0"]
    # ``get`` devuelve la última versión por defecto.
    assert store.get("vwap_dist").version == "2.0"


def test_get_or_compute_caches_within_validity():
    store = FeatureStore()
    store.register(_definition(validity_seconds=1000.0))
    calls = {"n": 0}

    def compute() -> float:
        calls["n"] += 1
        return 42.0

    first = store.get_or_compute("vwap_dist", "BTCUSDT", compute)
    second = store.get_or_compute("vwap_dist", "BTCUSDT", compute)
    assert first == second == 42.0
    assert calls["n"] == 1  # sólo se calculó una vez
    assert store.stats["hits"] == 1
    assert store.stats["misses"] == 1
    # Otra clave sí recalcula.
    store.get_or_compute("vwap_dist", "ETHUSDT", compute)
    assert calls["n"] == 2


def test_invalidate_forces_recompute():
    store = FeatureStore()
    store.register(_definition(validity_seconds=1000.0))
    calls = {"n": 0}

    def compute() -> int:
        calls["n"] += 1
        return calls["n"]

    store.get_or_compute("vwap_dist", "BTCUSDT", compute)
    store.invalidate("vwap_dist")
    store.get_or_compute("vwap_dist", "BTCUSDT", compute)
    assert calls["n"] == 2


def test_unknown_feature_raises():
    store = FeatureStore()
    with pytest.raises(MLError):
        store.get("does_not_exist")

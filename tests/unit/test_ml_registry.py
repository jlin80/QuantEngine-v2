"""Model Registry: versionado, activación, rollback y persistencia (Fase 7)."""

import pytest
from app.config.settings import MLModelSettings
from app.core.exceptions import ModelRegistryError
from app.ml.interfaces.model import ModelType
from app.ml.models.factory import build_model
from app.ml.registry import ModelRegistry, ModelState


def _fitted_model(kind: str = "logistic_regression"):
    model = build_model(kind, MLModelSettings(), seed=7)
    model.fit([[0.0, 0.0], [1.0, 1.0], [0.2, 0.1], [0.9, 0.8]], [0, 1, 0, 1])
    return model


def _register(registry: ModelRegistry, kind: str = "logistic_regression", *, auc: float = 0.7):
    return registry.register(
        _fitted_model(kind),
        metrics={"auc": auc, "accuracy": 0.66},
        dataset={"samples": 80},
        feature_names=["a", "b"],
    )


def test_register_bumps_version_and_never_overwrites():
    registry = ModelRegistry(None)
    first = _register(registry)
    second = _register(registry)
    assert first.id != second.id  # nunca sobrescribe: cada registro es nuevo
    assert first.version == "1.0"
    assert second.version == "2.0"
    assert registry.count() == 2
    assert {r.id for r in registry.records()} == {first.id, second.id}


def test_activation_archives_previous_and_tracks_active():
    registry = ModelRegistry(None)
    first = _register(registry)
    second = _register(registry)

    registry.activate(first.id)
    assert (active := registry.active_record()) is not None and active.id == first.id
    assert registry.get(first.id).state is ModelState.ACTIVE

    registry.activate(second.id)
    assert (active := registry.active_record()) is not None and active.id == second.id
    # El anterior se archiva (deja de estar activo).
    assert registry.get(first.id).active is False
    assert registry.get(first.id).state is ModelState.ARCHIVED


def test_rollback_restores_previous_model_immediately():
    registry = ModelRegistry(None)
    first = _register(registry)
    second = _register(registry)
    registry.activate(first.id)
    registry.activate(second.id)

    restored = registry.rollback()
    assert restored is not None
    assert restored.id == first.id
    assert (active := registry.active_record()) is not None and active.id == first.id
    assert registry.get(second.id).state is ModelState.ARCHIVED


def test_rollback_without_history_returns_none():
    registry = ModelRegistry(None)
    _register(registry)
    assert registry.rollback() is None


def test_approve_and_reject_set_state():
    registry = ModelRegistry(None)
    record = _register(registry)
    registry.approve(record.id, reasons="supera los mínimos")
    assert registry.get(record.id).state is ModelState.APPROVED
    registry.reject(record.id, reasons="deja de batir al activo")
    assert registry.get(record.id).state is ModelState.REJECTED


def test_get_unknown_model_raises():
    registry = ModelRegistry(None)
    with pytest.raises(ModelRegistryError):
        registry.get("nope")


def test_audit_trail_records_decisions():
    registry = ModelRegistry(None)
    record = _register(registry)
    registry.activate(record.id)
    actions = [entry["action"] for entry in registry.history()]
    assert "register" in actions
    assert "activate" in actions


def test_metadata_persists_and_reloads(tmp_path):
    directory = tmp_path / "registry"
    registry = ModelRegistry(directory)
    first = _register(registry)
    second = _register(registry)
    registry.activate(first.id)
    registry.activate(second.id)

    # Un registro nuevo sobre la misma carpeta rehidrata los metadatos.
    reloaded = ModelRegistry(directory)
    assert reloaded.count() == 2
    assert (active := reloaded.active_record()) is not None and active.id == second.id
    assert reloaded.status()["can_rollback"] is True
    # El objeto vivo no se rehidrata (sólo metadatos): se degrada con elegancia.
    assert reloaded.active_model() is None


def test_status_reports_counts_by_state():
    registry = ModelRegistry(None)
    record = _register(registry)
    registry.activate(record.id)
    status = registry.status()
    assert status["count"] == 1
    assert status["active"]["id"] == record.id
    assert status["by_state"].get("active") == 1


def test_build_model_covers_every_native_type():
    for kind in (
        ModelType.LOGISTIC_REGRESSION,
        ModelType.DECISION_TREE,
        ModelType.RANDOM_FOREST,
        ModelType.EXTRA_TREES,
    ):
        model = build_model(kind, MLModelSettings(), seed=7)
        assert model.model_type is kind

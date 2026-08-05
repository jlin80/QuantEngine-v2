"""Integración del Feature Importance Tracker (Bloque 13): ML y API."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.ml.api import MLEngine
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _engine() -> MLEngine:
    settings = Settings()
    settings.ml.importance.min_sample = 10
    settings.ml.importance.repeats = 2
    return MLEngine(settings, trades_provider=lambda: [], persist=False)


def test_without_an_active_model_nothing_is_measured() -> None:
    # Un ranking de ceros parecería una medición; el motivo no.
    report = _engine().run_importance()
    assert report.observable is False
    assert "sin modelo activo" in report.reason


def _client(wired: bool) -> TestClient:
    container = Container()
    if wired:
        container.register_instance(MLEngine, _engine())
    return TestClient(create_app(Settings(), container=container))


def test_the_importance_endpoints_answer_without_a_model() -> None:
    client = _client(True)
    assert client.get("/api/ml/importance").json()["method"] == "permutation"
    body = client.post("/api/ml/importance/run").json()
    assert body["observable"] is False
    assert body["features"] == []


def test_importance_endpoints_answer_503_when_ml_is_off() -> None:
    assert _client(False).get("/api/ml/importance").status_code == 503

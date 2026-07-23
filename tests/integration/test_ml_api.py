"""Integración: endpoints /api/ml/* y cableado del ML en el contenedor DI."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.bootstrap import _build_ml
from app.execution.journal import TradeJournal
from app.ml.api import MLEngine
from app.ml.notifications import MLNotifier
from app.notifications.service import NotificationService
from fastapi.testclient import TestClient

from tests.unit.ml_helpers import context, learnable_trades

pytestmark = pytest.mark.integration


def _engine_with_active_model() -> MLEngine:
    """An MLEngine over learnable trades with one trained, active model."""
    engine = MLEngine(Settings(), trades_provider=lambda: learnable_trades(80), persist=False)
    record = engine.register_model(engine.train_model())
    engine.activate_model(record.id)
    return engine


@pytest.fixture()
def client(settings: Settings):
    container = Container()
    container.register_instance(MLEngine, _engine_with_active_model())
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_ml_status(client: TestClient):
    body = client.get("/api/ml/status").json()
    for key in ("enabled", "registry", "inference", "drift", "meta", "features"):
        assert key in body
    assert body["auto_activate"] is False  # seguridad: no autoactiva por defecto


def test_ml_report_and_models(client: TestClient):
    report = client.get("/api/ml/report").json()
    assert report["active_model"] is not None

    models = client.get("/api/ml/models").json()
    assert models["models"]
    assert models["active"] is not None
    assert isinstance(models["history"], list)


def test_ml_ranking_features_meta(client: TestClient):
    ranking = client.get("/api/ml/ranking").json()
    assert "ranking" in ranking

    features = client.get("/api/ml/features").json()
    assert features["features"]
    assert all("name" in entry for entry in features["features"])

    meta = client.get("/api/ml/meta").json()
    assert "weights" in meta


def test_ml_predict_is_explained(client: TestClient):
    body = client.post("/api/ml/predict", json=dict(context())).json()
    assert body["label"] in {"good", "bad"}
    assert body["explanation"]  # nunca sólo un número
    assert body["reasons"]


def test_ml_train_on_demand_is_paper_safe(client: TestClient):
    body = client.post("/api/ml/train").json()
    assert body["status"] in {"ok", "skipped"}


def test_ml_endpoints_503_without_engine(settings: Settings):
    app = create_app(settings, Container())
    with TestClient(app) as anon:
        assert anon.get("/api/ml/status").status_code == 503
        assert anon.post("/api/ml/predict", json={}).status_code == 503


# ---------------------------------------------------------------------------
# Cableado en el contenedor (bootstrap)
# ---------------------------------------------------------------------------


def test_build_ml_registers_engine_and_notifier():
    container = Container()
    container.register_instance(NotificationService, NotificationService())
    _build_ml(container, Settings(), EventBus())
    assert container.contains(MLEngine)
    assert container.contains(MLNotifier)


def test_build_ml_reads_from_the_trade_journal():
    container = Container()
    container.register_instance(NotificationService, NotificationService())
    journal = TradeJournal(None, persist=False)
    for trade in learnable_trades(4):
        journal.record(trade)
    container.register_instance(TradeJournal, journal)

    _build_ml(container, Settings(), EventBus())
    engine = container.resolve(MLEngine)
    # El historial del ML es el propio Trade Journal del motor (Fase 5).
    assert len(engine.labeled_trades()) == 4

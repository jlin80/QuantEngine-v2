"""Integración del Confidence Calibration Engine (Bloque 10): ML y API."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.ml.api import MLEngine
from fastapi.testclient import TestClient

from tests.unit.ml_helpers import make_trade

pytestmark = pytest.mark.integration


def _trades(count: int, confidence: float, win_every: int):
    """Operaciones cerradas con una confianza declarada y un acierto real."""
    return [
        make_trade(
            strategy="alpha",
            confidence=confidence,
            pnl=1.0 if index % win_every == 0 else -1.0,
        )
        for index in range(count)
    ]


def _engine(trades) -> MLEngine:
    settings = Settings()
    settings.ml.calibration.min_sample = 20
    settings.ml.calibration.min_bin_sample = 2
    return MLEngine(settings, trades_provider=lambda: trades, persist=False)


def test_calibration_reads_the_confidence_the_engine_actually_declared() -> None:
    # Declara 0.9 y acierta una de cada cuatro: sobreconfianza medible.
    engine = _engine(_trades(80, confidence=0.9, win_every=4))
    report = engine.run_calibration()
    assert report.observable is True
    assert report.sample == 80
    assert report.bias is not None and report.bias < 0.0
    assert report.correction < 1.0


def test_the_correction_is_available_to_the_ml_layer_but_not_auto_applied() -> None:
    engine = _engine(_trades(80, confidence=0.9, win_every=4))
    assert engine.calibration.calibrated(0.9) == 0.9
    engine.run_calibration()
    assert engine.calibration.calibrated(0.9) < 0.9


def _client(wired: bool) -> TestClient:
    container = Container()
    if wired:
        container.register_instance(MLEngine, _engine(_trades(80, 0.9, 4)))
    return TestClient(create_app(Settings(), container=container))


def test_the_calibration_endpoints_expose_the_diagnosis() -> None:
    client = _client(True)
    assert client.get("/api/ml/calibration").json()["last_report"] is None

    body = client.post("/api/ml/calibration/run").json()
    assert body["observable"] is True
    assert body["reliability_diagram"]
    assert body["ece"] is not None
    assert body["correction"] < 1.0

    assert client.get("/api/ml/calibration").json()["last_report"] is not None


def test_calibration_endpoints_answer_503_when_ml_is_off() -> None:
    assert _client(False).get("/api/ml/calibration").status_code == 503

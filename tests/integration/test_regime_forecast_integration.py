"""Integración del Regime Forecast Engine (Bloque 4): servicio, bus y API."""

import asyncio

import pytest
from app.config.settings import QuantRegimeForecastSettings, QuantRegimeSettings, Settings
from app.core.container import Container
from app.core.events.bus import EventBus
from app.dashboard.api.main import create_app
from app.engine.events import RegimeForecastUpdated
from app.engine.regime_detection import RegimeDetector
from app.engine.regime_forecast import RegimeForecastEngine, RegimeForecastService
from app.market.models import Timeframe
from fastapi.testclient import TestClient

from tests.unit.quant_helpers import make_market
from tests.unit.test_regime_forecast import _candles

pytestmark = pytest.mark.integration


def _service(bus: EventBus | None = None, closes: list[float] | None = None):
    candles = _candles(closes or [100.0 + (i % 5) * 0.4 for i in range(300)])
    market = make_market(candles=candles)
    settings = QuantRegimeForecastSettings(horizon_bars=3, min_sample=5, confidence_sample=20)
    engine = RegimeForecastEngine(settings)
    detector = RegimeDetector(market, QuantRegimeSettings())
    return RegimeForecastService(
        settings,
        engine,
        detector,
        market,
        ["BTCUSDT"],
        timeframe=Timeframe.M5,
        bus=bus,
    )


def test_bootstrap_seeds_the_engine_from_history() -> None:
    # Sin sembrar, el motor tarda semanas en poder pronosticar nada.
    service = _service()
    assert service.bootstrap() > 0
    assert service.status()["engine"]["observations"] > 0


def test_a_cycle_forecasts_every_tracked_symbol() -> None:
    service = _service()
    service.bootstrap()
    forecasts = service.cycle()
    assert "BTCUSDT" in forecasts
    assert forecasts["BTCUSDT"].observable is True
    assert sum(forecasts["BTCUSDT"].probabilities.values()) == pytest.approx(1.0)


def test_a_forecast_is_not_validated_against_itself() -> None:
    # El ciclo resuelve ANTES de emitir: al revés, el pronóstico recién emitido
    # entraría en su propia validación.
    service = _service()
    service.bootstrap()
    service.cycle()
    first = service.status()["engine"]["score"]["resolved"]
    service.cycle()
    assert service.status()["engine"]["score"]["resolved"] >= first


def test_a_symbol_without_candles_is_skipped_not_guessed() -> None:
    market = make_market(candles=[])
    settings = QuantRegimeForecastSettings(horizon_bars=3, min_sample=5)
    service = RegimeForecastService(
        settings,
        RegimeForecastEngine(settings),
        RegimeDetector(market, QuantRegimeSettings()),
        market,
        ["BTCUSDT"],
        timeframe=Timeframe.M5,
    )
    assert service.cycle() == {}
    assert service.last("BTCUSDT") is None


@pytest.mark.asyncio
async def test_the_forecast_carries_its_skill_onto_the_bus() -> None:
    # Quien lea el evento sabe de inmediato si este motor ha demostrado valer
    # más que el pronóstico trivial.
    bus = EventBus()
    await bus.start()
    seen: list[RegimeForecastUpdated] = []

    async def collect(event: RegimeForecastUpdated) -> None:
        seen.append(event)

    bus.subscribe(collect, RegimeForecastUpdated)
    try:
        service = _service(bus)
        service.bootstrap()
        await service.run_cycle()
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert len(seen) == 1
    assert seen[0].symbol == "BTCUSDT"
    assert seen[0].most_likely
    assert 0.0 <= seen[0].probability <= 1.0


@pytest.mark.asyncio
async def test_an_unobservable_forecast_is_not_announced() -> None:
    bus = EventBus()
    await bus.start()
    seen: list[RegimeForecastUpdated] = []

    async def collect(event: RegimeForecastUpdated) -> None:
        seen.append(event)

    bus.subscribe(collect, RegimeForecastUpdated)
    try:
        await _service(bus).run_cycle()  # sin bootstrap: no hay muestra
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert seen == []


def _client(service: RegimeForecastService | None) -> TestClient:
    container = Container()
    if service is not None:
        container.register_instance(RegimeForecastService, service)
    return TestClient(create_app(Settings(), container=container))


def test_forecast_endpoints_expose_probabilities_and_validation() -> None:
    service = _service()
    service.bootstrap()
    client = _client(service)

    assert client.get("/api/forecast/BTCUSDT").status_code == 404  # aún sin ciclo

    cycle = client.post("/api/forecast/cycle").json()
    assert cycle["BTCUSDT"]["observable"] is True

    body = client.get("/api/forecast/BTCUSDT").json()
    assert body["most_likely"] in body["probabilities"]

    status = client.get("/api/forecast/status").json()
    assert status["cycles"] == 1
    assert "score" in status["engine"]


def test_forecast_endpoints_answer_503_when_not_wired() -> None:
    assert _client(None).get("/api/forecast/status").status_code == 503

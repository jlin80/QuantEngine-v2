"""Integración: composition root + ciclo de vida completo del motor."""

import asyncio

import pytest
from app.cache.service import CacheService
from app.config.settings import Settings
from app.core.container import Container
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.events.events import SystemStarted
from app.core.lifecycle import ServiceState
from app.dashboard.api.service import ApiService
from app.database.engine import DatabaseManager
from app.documentation.service import DocumentationService
from app.engine.bootstrap import build_container
from app.engine.decision_engine import DecisionEngine
from app.engine.engine import QuantEngine
from app.engine.feature_store import FeatureStore
from app.engine.models import SignalStatus
from app.engine.quant_core import QuantCore
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import HistoryWriter, SignalHistoryStore
from app.engine.strategy_engine import StrategyEngine
from app.market.services import MarketDataService
from app.monitoring.health import HealthMonitor
from app.monitoring.watchdog import Watchdog
from app.notifications.service import NotificationService
from app.scheduler.scheduler import AsyncScheduler

from tests.unit.quant_helpers import make_signal

pytestmark = pytest.mark.integration


@pytest.fixture()
def engine_settings(settings: Settings) -> Settings:
    """Settings de testing con puerto efímero para no chocar con un backend local."""
    dashboard = settings.dashboard.model_copy(update={"api_port": 0})
    return settings.model_copy(update={"dashboard": dashboard})


@pytest.fixture()
def container(engine_settings: Settings) -> Container:
    return build_container(engine_settings)


def test_build_container_wires_every_service(container: Container, engine_settings: Settings):
    for service_type in (
        Settings,
        EventBus,
        CacheService,
        DatabaseManager,
        NotificationService,
        AsyncScheduler,
        Watchdog,
        HealthMonitor,
        DocumentationService,
        ApiService,
    ):
        assert container.contains(service_type), service_type.__name__
        # Singletons: resolver dos veces devuelve la misma instancia.
        assert container.resolve(service_type) is container.resolve(service_type)
    assert container.resolve(Settings) is engine_settings


def test_notifications_have_no_channels_in_testing(container: Container):
    # El overlay de testing desactiva Discord: no debe registrarse el canal.
    notifications = container.resolve(NotificationService)
    assert notifications.channel_names == []


async def test_engine_full_lifecycle(engine_settings: Settings, container: Container):
    engine = QuantEngine(engine_settings, container)
    bus = container.resolve(EventBus)

    dispatched: list[Event] = []

    async def collector(event: Event) -> None:
        dispatched.append(event)

    bus.subscribe(collector, SystemStarted)

    await engine.start()
    try:
        for service_type in (
            EventBus,
            CacheService,
            NotificationService,
            AsyncScheduler,
            Watchdog,
            HealthMonitor,
            ApiService,
        ):
            assert container.resolve(service_type).is_running, service_type.__name__

        # SystemStarted se despacha de forma asíncrona por el worker del bus.
        for _ in range(100):
            if dispatched:
                break
            await asyncio.sleep(0.02)
        assert len(dispatched) == 1
        started = dispatched[0]
        assert isinstance(started, SystemStarted)
        assert started.environment == "testing"

        # Todos los servicios (salvo el propio watchdog) quedan supervisados.
        watchdog = container.resolve(Watchdog)
        supervised = set(watchdog.component_statuses)
        assert supervised == {
            "event_bus",
            "cache",
            "notifications",
            "scheduler",
            "health_monitor",
            "api",
        }
    finally:
        await engine.stop()

    for service_type in (EventBus, CacheService, AsyncScheduler, Watchdog, ApiService):
        assert container.resolve(service_type).state is ServiceState.STOPPED


async def test_engine_stop_without_start_is_safe(engine_settings: Settings, container: Container):
    engine = QuantEngine(engine_settings, container)
    await engine.stop()  # no debe lanzar aunque nada haya arrancado
    assert container.resolve(EventBus).state is ServiceState.CREATED


async def test_run_forever_unblocks_on_request_stop(
    engine_settings: Settings, container: Container
):
    engine = QuantEngine(engine_settings, container)
    runner = asyncio.create_task(engine.run_forever())
    await asyncio.sleep(0.01)
    assert not runner.done()
    engine.request_stop()
    await asyncio.wait_for(runner, timeout=1.0)


def test_build_container_wires_quant_core(engine_settings: Settings):
    """Con market+quant habilitados el composition root cablea el cerebro.

    Solo construcción (los constructores son pasivos): no se abre ninguna
    conexión a exchanges ni a la base de datos.
    """
    market = engine_settings.market.model_copy(update={"enabled": True})
    quant = engine_settings.quant.model_copy(update={"enabled": True})
    settings = engine_settings.model_copy(update={"market": market, "quant": quant})

    container = build_container(settings)

    for service_type in (
        MarketDataService,
        QuantCore,
        StrategyEngine,
        SignalEngine,
        DecisionEngine,
        FeatureStore,
        SignalHistoryStore,
        HistoryWriter,
    ):
        assert container.contains(service_type), service_type.__name__

    # El sink historial -> writer queda cableado: registrar una señal la encola.
    history = container.resolve(SignalHistoryStore)
    writer = container.resolve(HistoryWriter)
    history.record_signal(make_signal(), SignalStatus.REJECTED, ("prueba de sink",))
    assert writer.status()["pending_signals"] == 1

    # El motor arma su lista de servicios (writer + strategy engine) sin errores.
    engine = QuantEngine(settings, container)
    assert engine.container is container

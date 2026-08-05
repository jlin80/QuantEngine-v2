"""Integración del Data Quality Engine (Bloque 11): sizing, bus y cableado."""

import asyncio

import pytest
from app.config.settings import DataQualitySettings, Settings, SizingSettings
from app.core.events.bus import EventBus
from app.execution.sizing import PositionSizer
from app.monitoring.data_quality import DataQualityEngine
from app.monitoring.data_quality_service import DataQualityMonitor
from app.monitoring.events import DataQualityDegraded

from tests.unit.test_data_quality import _healthy

pytestmark = pytest.mark.integration


def _engine(inputs, **overrides) -> DataQualityEngine:
    return DataQualityEngine(DataQualitySettings(**overrides), lambda: inputs)


# ---------------------------------------------------------------------------
# Efecto real sobre el tamaño de la posición
# ---------------------------------------------------------------------------


def _sizer() -> PositionSizer:
    return PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=1.0, min_quantity=0.001)
    )


def test_a_degraded_feed_actually_shrinks_the_position() -> None:
    sizer = _sizer()
    full = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0)
    reduced = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0, risk_multiplier=0.5)
    assert reduced.quantity < full.quantity
    assert reduced.quantity == pytest.approx(full.quantity * 0.5, rel=0.05)


def test_the_multiplier_can_never_increase_the_position() -> None:
    # Dejar que alguien lo use para subir el tamaño sería abrir una puerta
    # trasera al sizing.
    sizer = _sizer()
    full = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0)
    inflated = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0, risk_multiplier=5.0)
    assert inflated.quantity == full.quantity


def test_shrinking_below_the_minimum_rejects_cleanly() -> None:
    # Si el dato no es fiable y el tamaño reducido ya no cabe, no se opera.
    sizer = _sizer()
    result = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0, risk_multiplier=1e-6)
    assert result.quantity == 0.0
    assert result.reason


def test_the_default_multiplier_leaves_sizing_untouched() -> None:
    sizer = _sizer()
    assert (
        sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0).quantity
        == sizer.calculate(
            equity=10_000.0, price=100.0, stop_distance=1.0, risk_multiplier=1.0
        ).quantity
    )


# ---------------------------------------------------------------------------
# Servicio y bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degradation_and_recovery_are_both_announced_once() -> None:
    # No anunciar la recuperación dejaría a quien leyó la alarma creyendo que el
    # problema sigue; repetirla cada minuto la convertiría en ruido.
    bus = EventBus()
    await bus.start()
    seen: list[DataQualityDegraded] = []

    async def collect(event: DataQualityDegraded) -> None:
        seen.append(event)

    bus.subscribe(collect, DataQualityDegraded)
    state = {"inputs": _healthy(symbols_with_data=0)}
    engine = DataQualityEngine(DataQualitySettings(), lambda: state["inputs"])
    monitor = DataQualityMonitor(DataQualitySettings(), engine, bus)
    try:
        await monitor.run_cycle()
        await monitor.run_cycle()  # sigue degradado: no repite
        state["inputs"] = _healthy()
        await monitor.run_cycle()  # recuperado: sí anuncia
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()

    assert [event.degraded for event in seen] == [True, False]


@pytest.mark.asyncio
async def test_the_monitor_measures_the_moment_it_starts() -> None:
    # Arrancar con el multiplicador en 1.0 durante un ciclo entero es arrancar
    # sin la protección, justo cuando el feed aún se estabiliza.
    engine = _engine(_healthy(symbols_with_data=0))
    monitor = DataQualityMonitor(DataQualitySettings(cycle_interval_seconds=3600.0), engine)
    await monitor.start()
    try:
        assert engine.last_report() is not None
        assert engine.risk_multiplier() < 1.0
    finally:
        await monitor.stop()


def test_the_engine_is_wired_from_the_real_composition_root() -> None:
    from app.engine.bootstrap import build_container

    settings = Settings()
    settings.market.enabled = True
    container = build_container(settings)
    assert container.contains(DataQualityEngine)
    assert container.contains(DataQualityMonitor)

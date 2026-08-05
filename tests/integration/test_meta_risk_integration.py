"""Integración del Meta Risk Engine (Bloque 12): ciclo compartido y cableado."""

import pytest
from app.config.settings import DataQualitySettings, MetaRiskSettings, Settings
from app.monitoring.data_quality import DataQualityEngine
from app.monitoring.data_quality_service import DataQualityMonitor
from app.monitoring.meta_risk import MetaRiskEngine

from tests.unit.test_data_quality import _healthy as _healthy_data
from tests.unit.test_meta_risk import _healthy as _healthy_infra

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_meta_risk_consumes_the_data_quality_of_the_same_cycle() -> None:
    # Medirlo en un bucle propio significaría componer con una lectura de
    # calidad de hasta un minuto de antigüedad, justo cuando lo que cambia
    # rápido es la calidad.
    quality = DataQualityEngine(DataQualitySettings(), lambda: _healthy_data(symbols_with_data=0))
    meta = MetaRiskEngine(
        MetaRiskSettings(),
        lambda: _healthy_infra(data_quality_multiplier=quality.risk_multiplier()),
    )
    monitor = DataQualityMonitor(DataQualitySettings(), quality, None, meta)

    await monitor.run_cycle()

    assert quality.risk_multiplier() < 1.0
    assert meta.risk_multiplier() == pytest.approx(quality.risk_multiplier(), abs=0.01)


@pytest.mark.asyncio
async def test_a_healthy_system_keeps_full_exposure_end_to_end() -> None:
    quality = DataQualityEngine(DataQualitySettings(), lambda: _healthy_data())
    meta = MetaRiskEngine(
        MetaRiskSettings(),
        lambda: _healthy_infra(data_quality_multiplier=quality.risk_multiplier()),
    )
    monitor = DataQualityMonitor(DataQualitySettings(), quality, None, meta)
    await monitor.run_cycle()
    assert meta.risk_multiplier() == 1.0


def test_the_execution_engine_reads_the_composed_multiplier() -> None:
    # Es el multiplicador COMPUESTO el que llega a la ejecución, no sólo el de
    # calidad de dato: si no, la mitad de la protección no llegaría a aplicarse.
    from app.engine.bootstrap import build_container
    from app.execution.execution_engine import ExecutionEngine

    settings = Settings()
    settings.market.enabled = True
    settings.quant.enabled = True
    settings.execution.enabled = True
    container = build_container(settings)

    assert container.contains(MetaRiskEngine)
    engine = container.resolve(ExecutionEngine)
    reader = engine._risk_multiplier_reader
    assert reader is not None
    assert reader() == 1.0
    assert reader.__self__ is container.resolve(MetaRiskEngine)  # type: ignore[attr-defined]


def test_meta_risk_is_wired_only_with_the_data_engine_on() -> None:
    from app.engine.bootstrap import build_container

    off = Settings()
    off.market.enabled = False
    assert not build_container(off).contains(MetaRiskEngine)

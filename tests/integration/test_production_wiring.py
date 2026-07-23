"""Integración: la capa de producción se cablea y arranca dentro del motor real."""

import pytest
from app.config.settings import Settings
from app.engine.bootstrap import build_container
from app.engine.engine import QuantEngine
from app.execution.execution_engine import ExecutionEngine
from app.production.api import ProductionAPI
from app.production.kill_switch import KillSwitchController
from app.production.recovery import RecoveryService
from app.production.safe_mode import SafeModeController

pytestmark = pytest.mark.integration


@pytest.fixture()
def production_settings(settings: Settings, tmp_path) -> Settings:
    """Settings con producción y ejecución habilitadas, con estado en tmp_path."""
    updated = settings.model_copy(deep=True)
    updated.production.enabled = True
    updated.production.state_dir = tmp_path
    updated.production.audit_path = tmp_path / "audit.jsonl"
    updated.production.approval_path = tmp_path / "approval.json"
    updated.production.kill_switch.state_path = tmp_path / "kill.json"
    updated.production.recovery.snapshot_path = tmp_path / "state.json"
    updated.execution.enabled = True
    updated.execution.persist_journal = False
    updated.market.enabled = True
    return updated


def test_production_layer_is_wired(production_settings: Settings):
    container = build_container(production_settings)
    for component in (
        ProductionAPI,
        SafeModeController,
        KillSwitchController,
        RecoveryService,
    ):
        assert container.contains(component), component.__name__


def test_recovery_starts_before_execution(production_settings: Settings):
    """Rehidratar posiciones después de arrancar el motor sería una carrera."""
    container = build_container(production_settings)
    engine = QuantEngine(production_settings, container)
    names = [service.name for service in engine._services]
    assert names.index("recovery") < names.index("execution_engine")
    assert names.index("execution_engine") < names.index("kill_switch")


def test_safe_mode_veto_is_registered(production_settings: Settings):
    container = build_container(production_settings)
    execution = container.resolve(ExecutionEngine)
    safe_mode = container.resolve(SafeModeController)
    assert execution._entry_veto() is None

    safe_mode._active = True
    safe_mode._detail = "prueba"
    veto = execution._entry_veto()
    assert veto is not None and "Safe Mode" in veto


def test_mode_stays_paper_after_full_boot(production_settings: Settings):
    """El invariante de la fase, comprobado sobre el grafo real de dependencias."""
    container = build_container(production_settings)
    production = container.resolve(ProductionAPI)
    assert production.mode.resolved_mode() == "paper"
    assert production_settings.execution.resolved_mode() == "paper"
    assert container.resolve(ExecutionEngine).broker.broker_name == "paper"


def test_live_gate_cannot_approve_out_of_the_box(production_settings: Settings):
    container = build_container(production_settings)
    production = container.resolve(ProductionAPI)
    report = production.gate.evaluate(production.collect_gate_inputs())
    assert not report.approved
    assert any("allow_live" in reason for reason in report.reasons)

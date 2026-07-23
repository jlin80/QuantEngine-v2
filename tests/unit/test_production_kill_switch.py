"""Kill Switch: dispara desde fuera, sobrevive al reinicio y se audita entero."""

from datetime import UTC, datetime

import pytest
from app.config.settings import ExecutionRiskSettings
from app.execution.risk_manager import RiskManager, RiskQuery
from app.production.kill_switch import KillSwitchTrigger

from tests.unit.production_helpers import make_kill_switch


def _risk() -> RiskManager:
    return RiskManager(ExecutionRiskSettings(), 10_000.0)


def _query() -> RiskQuery:
    return RiskQuery(
        symbol="BTCUSDT",
        new_notional=100.0,
        equity=10_000.0,
        open_positions=0,
        positions_on_symbol=0,
    )


async def test_manual_engage_blocks_entries(tmp_path):
    risk = _risk()
    controller = make_kill_switch(tmp_path, risk)
    assert risk.evaluate_entry(_query()).allowed

    await controller.engage(KillSwitchTrigger.MANUAL, "prueba", actor="jlin")
    assert controller.active
    check = risk.evaluate_entry(_query())
    assert not check.allowed and check.rule == "kill_switch"


async def test_every_trigger_source_works(tmp_path):
    for trigger in KillSwitchTrigger:
        controller = make_kill_switch(tmp_path / str(trigger), _risk())
        await controller.engage(trigger, f"motivo {trigger}")
        assert controller.switch_state.trigger == str(trigger)


async def test_state_survives_a_restart(tmp_path):
    """El agujero que cerró la Fase 9: reiniciar liberaba el kill switch."""
    first = make_kill_switch(tmp_path, _risk())
    await first.engage(KillSwitchTrigger.RISK, "drawdown crítico", actor="system")
    await first.stop()

    risk = _risk()
    second = make_kill_switch(tmp_path, risk)
    await second.start()
    assert second.active
    assert not risk.evaluate_entry(_query()).allowed
    await second.stop()


async def test_release_requires_actor_and_reason(tmp_path):
    controller = make_kill_switch(tmp_path, _risk())
    await controller.engage(KillSwitchTrigger.MANUAL, "prueba")
    with pytest.raises(ValueError):
        await controller.release(actor="", reason="ya está")
    with pytest.raises(ValueError):
        await controller.release(actor="jlin", reason="")
    assert controller.active


async def test_release_clears_the_switch(tmp_path):
    risk = _risk()
    controller = make_kill_switch(tmp_path, risk)
    await controller.engage(KillSwitchTrigger.MANUAL, "prueba")
    await controller.release(actor="jlin", reason="causa resuelta")
    assert not controller.active
    assert risk.evaluate_entry(_query()).allowed


async def test_engage_is_idempotent_and_keeps_the_origin(tmp_path):
    controller = make_kill_switch(tmp_path, _risk())
    await controller.engage(KillSwitchTrigger.LATENCY, "primera causa")
    await controller.engage(KillSwitchTrigger.MANUAL, "segunda causa")
    assert controller.switch_state.reason == "primera causa"


async def test_scheduled_cut_off(tmp_path):
    controller = make_kill_switch(tmp_path, _risk(), scheduled_utc="03:30")
    off_hour = datetime(2026, 7, 18, 3, 29, tzinfo=UTC)
    assert not await controller.check_scheduled(now=off_hour)

    on_hour = datetime(2026, 7, 18, 3, 30, tzinfo=UTC)
    assert await controller.check_scheduled(now=on_hour)
    assert controller.switch_state.trigger == str(KillSwitchTrigger.SCHEDULED)


async def test_invalid_schedule_is_ignored_not_fatal(tmp_path):
    controller = make_kill_switch(tmp_path, _risk(), scheduled_utc="no-es-una-hora")
    assert not await controller.check_scheduled()
    assert not controller.active

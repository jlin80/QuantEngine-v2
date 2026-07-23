"""Safe Mode: entra ante cada disparador y sale sólo con recuperación sostenida."""

from app.production.safe_mode import SafeModeObservation, SafeModeTrigger

from tests.unit.production_helpers import make_safe_mode


async def test_healthy_system_stays_normal():
    controller = make_safe_mode()
    assert not await controller.evaluate(SafeModeObservation(cpu_pct=10.0, memory_pct=20.0))
    assert controller.entry_veto() is None


async def test_each_trigger_engages_safe_mode():
    cases = (
        (SafeModeObservation(api_ok=False), SafeModeTrigger.API_DOWN),
        (SafeModeObservation(broker_failures=5), SafeModeTrigger.BROKER_UNSTABLE),
        (SafeModeObservation(latency_ms=9_000.0), SafeModeTrigger.LATENCY),
        (SafeModeObservation(drawdown_pct=50.0), SafeModeTrigger.DRAWDOWN),
        (SafeModeObservation(memory_pct=99.0), SafeModeTrigger.MEMORY),
        (SafeModeObservation(cpu_pct=99.0), SafeModeTrigger.CPU),
        (SafeModeObservation(recent_errors=50), SafeModeTrigger.REPEATED_ERRORS),
        (SafeModeObservation(critical_drift=True), SafeModeTrigger.DRIFT),
        (SafeModeObservation(risk_breach=True), SafeModeTrigger.RISK),
    )
    for observation, expected in cases:
        controller = make_safe_mode()
        assert await controller.evaluate(observation)
        assert controller.trigger is expected


async def test_safe_mode_blocks_new_entries():
    controller = make_safe_mode()
    await controller.evaluate(SafeModeObservation(cpu_pct=99.0))
    veto = controller.entry_veto()
    assert veto is not None and "Safe Mode" in veto


async def test_exit_requires_sustained_recovery():
    """Un solo ciclo sano no basta: si no, el sistema oscilaría."""
    controller = make_safe_mode(recovery_cycles=3)
    await controller.evaluate(SafeModeObservation(cpu_pct=99.0))
    healthy = SafeModeObservation(cpu_pct=5.0)

    assert await controller.evaluate(healthy)  # 1º sano, sigue en safe
    assert await controller.evaluate(healthy)  # 2º sano, sigue en safe
    assert not await controller.evaluate(healthy)  # 3º sano → sale
    assert controller.entry_veto() is None


async def test_relapse_resets_the_recovery_streak():
    controller = make_safe_mode(recovery_cycles=3)
    await controller.evaluate(SafeModeObservation(cpu_pct=99.0))
    await controller.evaluate(SafeModeObservation(cpu_pct=5.0))
    await controller.evaluate(SafeModeObservation(cpu_pct=99.0))  # recaída
    assert await controller.evaluate(SafeModeObservation(cpu_pct=5.0))
    assert await controller.evaluate(SafeModeObservation(cpu_pct=5.0))
    assert controller.active  # la racha volvió a empezar


async def test_unknown_vitals_do_not_engage_safe_mode():
    """Un sensor que no reporta no debe degradar la operativa."""
    controller = make_safe_mode()
    assert not await controller.evaluate(SafeModeObservation())


async def test_disabled_controller_never_engages():
    controller = make_safe_mode(enabled=False)
    assert not await controller.evaluate(SafeModeObservation(cpu_pct=100.0))

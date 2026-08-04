"""Segunda capa del gate del ML: vigencia frente a las reglas de ejecución.

`min_cv_auc` evita activar un modelo malo. Esto detecta algo distinto: que un
modelo **bueno** dejó de representar cómo opera el motor porque cambiaron las
reglas bajo las que se entrenó. Sus métricas de validación no lo delatan — su
AUC sigue siendo el mismo número de ayer.

Regla que se fija aquí: el gate **alerta y sugiere reentrenar**, y nunca
desactiva el modelo ni dispara un reentrenamiento por su cuenta.
"""

import asyncio

from app.config.settings import Settings
from app.core.events.bus import EventBus
from app.ml.api import MLEngine
from app.ml.events import ModelRequiresRetraining
from app.ml.monitoring.execution_rules import (
    execution_rules_hash,
    execution_rules_snapshot,
)

from tests.unit.ml_helpers import strategy_trades


def _settings() -> Settings:
    settings = Settings()
    settings.ml.enabled = True
    return settings


def _engine(settings: Settings, bus: EventBus | None = None) -> MLEngine:
    trades = strategy_trades("order_block", 80, win_rate=0.6)
    return MLEngine(settings, bus=bus, trades_provider=lambda: trades, persist=False)


def _train_and_activate(engine: MLEngine) -> None:
    """Entrena, registra y activa un modelo real (no un doble)."""
    result = engine.train_model()
    record = engine.register_model(result)
    engine.activate_model(record.id)


# ----------------------------------------------------------------------
# La huella
# ----------------------------------------------------------------------


def test_the_hash_is_stable_for_unchanged_rules():
    settings = _settings()

    assert execution_rules_hash(settings.execution) == execution_rules_hash(settings.execution)


def test_changing_a_holding_rule_changes_the_hash():
    settings = _settings()
    before = execution_rules_hash(settings.execution)

    settings.execution.regime_change_min_holding_by_strategy["bos"] = 999.0

    assert execution_rules_hash(settings.execution) != before


def test_changing_sizing_trailing_or_risk_changes_the_hash():
    for mutate in (
        lambda s: setattr(s.execution.sizing, "risk_per_trade_pct", 1.5),
        lambda s: setattr(s.execution, "trailing_activate_r", 2.0),
        lambda s: setattr(s.execution.risk, "max_open_positions", 9),
    ):
        settings = _settings()
        before = execution_rules_hash(settings.execution)
        mutate(settings)
        assert execution_rules_hash(settings.execution) != before


def test_cosmetic_settings_do_not_change_the_hash():
    """Una alerta que salta por cambios inocuos se acaba ignorando."""
    settings = _settings()
    before = execution_rules_hash(settings.execution)

    settings.execution.report_interval_seconds = 900.0
    settings.execution.manage_interval_seconds = 5.0

    assert execution_rules_hash(settings.execution) == before


def test_the_snapshot_covers_the_four_rule_families_the_block_names():
    snapshot = execution_rules_snapshot(_settings().execution)

    assert {"holding", "trailing", "sizing", "risk"} <= set(snapshot)


# ----------------------------------------------------------------------
# La comprobación
# ----------------------------------------------------------------------


def test_without_an_active_model_there_is_nothing_to_check():
    check = _engine(_settings()).check_execution_rules()

    assert check.status == "no_model"
    assert check.needs_retraining is False


def test_a_model_trained_under_the_current_rules_is_valid():
    settings = _settings()
    engine = _engine(settings)
    _train_and_activate(engine)

    check = engine.check_execution_rules()

    assert check.status == "ok"
    assert check.needs_retraining is False


def test_changing_an_execution_rule_marks_the_active_model_as_stale():
    settings = _settings()
    engine = _engine(settings)
    _train_and_activate(engine)

    settings.execution.sizing.risk_per_trade_pct = 1.25

    check = engine.check_execution_rules()

    assert check.status == "stale"
    assert check.needs_retraining is True
    assert "sizing" in check.changed
    assert check.model_hash != check.current_hash


def test_the_check_says_which_rule_family_changed():
    """Un aviso que no señala la causa se ignora a la segunda vez."""
    settings = _settings()
    engine = _engine(settings)
    _train_and_activate(engine)

    settings.execution.regime_change_min_holding_by_strategy["bos"] = 42.0
    settings.execution.trailing_activate_r = 3.0

    check = engine.check_execution_rules()

    assert set(check.changed) == {"holding", "trailing"}


def test_a_model_registered_before_this_control_is_reported_as_unknown():
    """No se puede afirmar que un modelo sin huella siga siendo representativo."""
    settings = _settings()
    engine = _engine(settings)
    _train_and_activate(engine)
    record = engine.registry.active_record()
    assert record is not None
    record.execution_rules_hash = ""

    check = engine.check_execution_rules()

    assert check.status == "unknown"
    assert check.needs_retraining is False, "no se afirma que esté obsoleto, sólo que no consta"
    assert "reentrenar" in check.detail


# ----------------------------------------------------------------------
# El gate avisa, no actúa
# ----------------------------------------------------------------------


async def test_a_stale_model_raises_an_alert_and_stays_active():
    """El punto 2 del bloque: alerta + sugerencia, sin auto-activación agresiva."""
    settings = _settings()
    bus = EventBus()
    await bus.start()
    seen: list[ModelRequiresRetraining] = []

    async def capture(event: ModelRequiresRetraining) -> None:
        seen.append(event)

    bus.subscribe(capture, ModelRequiresRetraining)

    engine = _engine(settings, bus)
    _train_and_activate(engine)
    active_before = engine.registry.active_record()
    assert active_before is not None

    settings.execution.risk.max_open_positions = 12
    payload = await engine.run_execution_rules_check()
    await asyncio.sleep(0.05)
    await bus.stop()

    assert payload["needs_retraining"] is True
    assert len(seen) == 1
    assert "risk" in seen[0].changed
    # Y el modelo sigue exactamente igual: activo, y sin reentrenar.
    active_after = engine.registry.active_record()
    assert active_after is not None
    assert active_after.id == active_before.id
    assert active_after.active is True


async def test_no_alert_while_the_rules_have_not_changed():
    settings = _settings()
    bus = EventBus()
    await bus.start()
    seen: list[ModelRequiresRetraining] = []

    async def capture(event: ModelRequiresRetraining) -> None:
        seen.append(event)

    bus.subscribe(capture, ModelRequiresRetraining)

    engine = _engine(settings, bus)
    _train_and_activate(engine)
    await engine.run_execution_rules_check()
    await asyncio.sleep(0.05)
    await bus.stop()

    assert seen == []


def test_retraining_refreshes_the_stored_fingerprint():
    """Reentrenar tras el cambio vuelve a dejar el modelo en regla."""
    settings = _settings()
    engine = _engine(settings)
    _train_and_activate(engine)
    settings.execution.sizing.risk_per_trade_pct = 1.25
    assert engine.check_execution_rules().status == "stale"

    _train_and_activate(engine)

    assert engine.check_execution_rules().status == "ok"


# ----------------------------------------------------------------------
# Latch: una vez por situacion, no una por vuelta
# ----------------------------------------------------------------------


async def _alerts_over(engine: MLEngine, bus: EventBus, runs: int) -> int:
    seen: list[ModelRequiresRetraining] = []

    async def capture(event: ModelRequiresRetraining) -> None:
        seen.append(event)

    subscription = bus.subscribe(capture, ModelRequiresRetraining)
    for _ in range(runs):
        await engine.run_execution_rules_check()
    await asyncio.sleep(0.05)
    bus.unsubscribe(subscription)
    return len(seen)


async def test_a_persistent_stale_state_alerts_only_once():
    """Regresion: el job es horario y el estado dura hasta que un humano
    reentrena. Sin latch, un aviso util se vuelve ruido de fondo."""
    settings = _settings()
    bus = EventBus()
    await bus.start()
    engine = _engine(settings, bus)
    _train_and_activate(engine)
    settings.execution.sizing.risk_per_trade_pct = 1.25

    alerts = await _alerts_over(engine, bus, runs=24)
    await bus.stop()

    assert alerts == 1


async def test_a_model_without_a_fingerprint_also_alerts_only_once():
    """`unknown` es el estado de CUALQUIER modelo previo a este control: sin
    latch empezaba a spamear en el primer minuto y no paraba."""
    settings = _settings()
    bus = EventBus()
    await bus.start()
    engine = _engine(settings, bus)
    _train_and_activate(engine)
    record = engine.registry.active_record()
    assert record is not None
    record.execution_rules_hash = ""

    alerts = await _alerts_over(engine, bus, runs=24)
    await bus.stop()

    assert alerts == 1


async def test_a_new_rule_change_re_arms_the_alert():
    """El latch silencia lo repetido, no lo nuevo."""
    settings = _settings()
    bus = EventBus()
    await bus.start()
    engine = _engine(settings, bus)
    _train_and_activate(engine)

    settings.execution.sizing.risk_per_trade_pct = 1.25
    first = await _alerts_over(engine, bus, runs=3)
    settings.execution.risk.max_open_positions = 11
    second = await _alerts_over(engine, bus, runs=3)
    await bus.stop()

    assert first == 1
    assert second == 1, "un cambio distinto vuelve a avisar"


async def test_returning_to_ok_re_arms_the_latch():
    settings = _settings()
    bus = EventBus()
    await bus.start()
    engine = _engine(settings, bus)
    _train_and_activate(engine)
    settings.execution.sizing.risk_per_trade_pct = 1.25
    assert await _alerts_over(engine, bus, runs=3) == 1

    _train_and_activate(engine)  # reentrenado: vuelve a estar en regla
    assert await _alerts_over(engine, bus, runs=3) == 0

    settings.execution.sizing.risk_per_trade_pct = 2.0
    assert await _alerts_over(engine, bus, runs=3) == 1
    await bus.stop()


async def test_the_check_can_be_turned_off():
    settings = _settings()
    settings.ml.execution_rules_check.enabled = False
    bus = EventBus()
    await bus.start()
    engine = _engine(settings, bus)
    _train_and_activate(engine)
    settings.execution.sizing.risk_per_trade_pct = 1.25

    alerts = await _alerts_over(engine, bus, runs=5)
    await bus.stop()

    assert alerts == 0

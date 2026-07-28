"""MLEngine: fachada del Machine Learning de extremo a extremo (Fase 7).

Cubre el flujo completo (dataset → entrenar → validar → registrar → activar →
predecir → deriva → meta), la orquestación asíncrona que publica eventos y la
regla de seguridad de que **nunca se activa un modelo inferior**. El ML asesora,
no decide: ninguna función abre operaciones ni habilita live trading.
"""

import asyncio

from app.config.settings import Settings
from app.core.events.bus import EventBus
from app.ml.api import MLEngine

from tests.unit.ml_helpers import context, learnable_trades, make_training_result


def _engine(*, bus: EventBus | None = None) -> MLEngine:
    return MLEngine(
        Settings(),
        bus=bus,
        trades_provider=lambda: learnable_trades(80),
        persist=False,
    )


def test_full_train_validate_register_activate_predict_flow():
    engine = _engine()
    dataset = engine.build_dataset()
    assert len(dataset) == 80 and set(dataset.y) == {0, 1}

    result = engine.train_model()
    approved, reasons = engine.evaluate_model(result)
    assert approved is True  # primer modelo, supera los mínimos
    assert reasons

    record = engine.register_model(result)
    assert record.version == "1.0"
    activated = engine.activate_model(record.id)
    assert activated.active is True
    assert engine.inference.has_active_model

    prediction = engine.predict_trade_quality(context())
    assert prediction.label in {"good", "bad"}
    assert prediction.explanation and prediction.reasons  # explicable

    explained = engine.explain_prediction(context())
    assert "explanation" in explained and "probability" in explained


def test_never_activates_an_inferior_model():
    engine = _engine()
    result = engine.train_model()
    record = engine.register_model(result)
    engine.activate_model(record.id)

    active = engine.registry.active_record()
    assert active is not None
    active_auc = float(active.metrics["auc"])
    assert active_auc > 0.6  # sanity: el modelo activo es bueno
    worse = make_training_result(result.model, auc=active_auc - 0.05, accuracy=0.6)
    approved, reasons = engine.evaluate_model(worse)
    assert approved is False
    assert any("no bate" in reason for reason in reasons)


def test_rollback_through_the_facade():
    engine = _engine()
    first = engine.register_model(engine.train_model())
    second = engine.register_model(engine.train_model())
    engine.activate_model(first.id)
    engine.activate_model(second.id)

    restored = engine.rollback_model()
    assert restored is not None
    assert (active := engine.registry.active_record()) is not None and active.id == first.id


def test_assess_risk_is_advisory_only():
    engine = _engine()
    record = engine.register_model(engine.train_model())
    engine.activate_model(record.id)
    assessment = engine.assess_risk(context(), symbol="BTCUSDT", regime="trending")
    # Estima probabilidades pero nunca abre/cierra: sólo asesora.
    assert 0.0 <= assessment["p_success"] <= 1.0
    assert assessment["recommendation"] in {"favorable", "cauto", "desfavorable", "neutral"}
    assert assessment["reasons"]  # siempre explicado, nunca sólo un número


def test_status_and_report_are_dashboard_ready():
    engine = _engine()
    record = engine.register_model(engine.train_model())
    engine.activate_model(record.id)

    status = engine.status()
    for key in ("enabled", "registry", "inference", "drift", "meta", "features"):
        assert key in status
    assert status["auto_activate"] is False  # seguridad: no autoactiva por defecto

    report = engine.report()
    assert report["active_model"] is not None
    assert isinstance(report["models"], list) and report["models"]

    catalog = engine.feature_catalog()
    assert catalog and all("name" in entry for entry in catalog)


def test_detect_drift_returns_a_report():
    engine = _engine()
    report = engine.detect_drift()
    assert "has_drift" in report.to_dict()


def test_rank_and_weight_strategies():
    engine = _engine()
    scores = engine.rank_strategies()
    assert scores  # al menos la estrategia por defecto (portfolio)
    weight = engine.calculate_strategy_weight(scores[0].name)
    assert weight > 0.0
    params = engine.recommend_parameters()
    assert isinstance(params, dict)


# ---------------------------------------------------------------------------
# Orquestación asíncrona (scheduler) — publica eventos en el bus
# ---------------------------------------------------------------------------


async def _collect(bus: EventBus) -> list[str]:
    seen: list[str] = []

    async def handler(event) -> None:
        seen.append(event.name)

    bus.subscribe(handler)
    return seen


async def test_nightly_training_publishes_lifecycle_events():
    bus = EventBus()
    await bus.start()
    seen = await _collect(bus)
    engine = _engine(bus=bus)

    outcome = await engine.run_nightly_training()
    await asyncio.sleep(0.05)
    await bus.stop()

    assert outcome["status"] == "ok"
    assert "ModelTrainingStarted" in seen
    assert "ModelTrainingFinished" in seen
    # Aprobado o rechazado, pero siempre uno de los dos hitos.
    assert "ModelApproved" in seen or "ModelRejected" in seen


async def test_nightly_training_does_not_auto_activate_by_default():
    bus = EventBus()
    await bus.start()
    engine = _engine(bus=bus)
    await engine.run_nightly_training()
    await asyncio.sleep(0.05)
    await bus.stop()
    # auto_activate=False: aunque apruebe, no hay modelo activo sin decisión humana.
    assert not engine.inference.has_active_model


async def test_meta_evaluation_and_drift_check_run():
    bus = EventBus()
    await bus.start()
    engine = _engine(bus=bus)
    meta = await engine.run_meta_evaluation()
    drift = await engine.run_drift_check()
    await asyncio.sleep(0.05)
    await bus.stop()
    assert "weights" in meta
    assert drift["status"] == "ok"


def test_gate_rejects_an_overfitted_model():
    """Holdout bueno + validacion cruzada peor que el azar = sobreajuste.

    Es el caso real que se colo en produccion: holdout 0.663, cv 0.463, y la
    puerta lo aprobo con "supera los minimos (AUC 0.663)" porque usaba
    `max(holdout, cv)` y descartaba la CV justo cuando contradecia.
    """
    engine = _engine()
    result = engine.train_model()
    overfitted = make_training_result(result.model, auc=0.663, accuracy=0.62, cv_auc=0.463)

    approved, reasons = engine.evaluate_model(overfitted)

    assert approved is False
    assert any("validacion cruzada" in r or "validación cruzada" in r for r in reasons)


def test_gate_accepts_when_cross_validation_also_holds_up():
    """Un modelo con holdout y CV consistentes si pasa."""
    engine = _engine()
    result = engine.train_model()
    solid = make_training_result(result.model, auc=0.68, accuracy=0.65, cv_auc=0.61)

    approved, reasons = engine.evaluate_model(solid)

    assert approved is True
    assert any("supera los" in r for r in reasons)


def test_gate_no_longer_lets_cv_rescue_a_weak_holdout():
    """Antes `max(holdout, cv)` permitia que una CV alta tapase un holdout malo."""
    engine = _engine()
    result = engine.train_model()
    weak = make_training_result(result.model, auc=0.50, accuracy=0.62, cv_auc=0.72)

    approved, reasons = engine.evaluate_model(weak)

    assert approved is False
    assert any("AUC 0.500" in r for r in reasons)

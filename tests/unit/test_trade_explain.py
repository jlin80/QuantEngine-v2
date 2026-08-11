"""Explicación por operación (Bloque 6): reúne evidencia, no la inventa."""

from datetime import UTC, datetime

from app.engine.models import Decision, DecisionAction, Direction
from app.engine.models.models import ConsensusResult
from app.engine.trade_explain import SignalVerdict, TradeExplainer
from app.execution.models import ExitReason, PositionSide, TradeRecord

_NOW = datetime(2026, 8, 11, 14, 30, tzinfo=UTC)


def _trade(**overrides) -> TradeRecord:
    base = {
        "trade_id": "t1",
        "position_id": "p1",
        "symbol": "XAUUSDM",
        "side": PositionSide.LONG,
        "quantity": 0.01,
        "contract_size": 100.0,
        "entry_time": _NOW,
        "exit_time": _NOW,
        "entry_price": 4400.0,
        "exit_price": 4402.0,
        "pnl": 2.0,
        "r_multiple": 0.4,
        "regime": "trending",
        "volatility": "normal",
        "strategy": "bos",
        "strategy_category": "smc",
        "score": 0.7,
        "confidence": 0.6,
        "exit_reason": ExitReason.REGIME_CHANGE,
        "decision_id": "d1",
        "signal_ids": ("s1", "s2"),
        "context_snapshot": {
            "entry_regime": "trending",
            "exit_regime": "ranging",
            "entry_sessions": ["europe", "america"],
            "holding_seconds": 300.0,
            "min_holding_seconds": 120.0,
        },
    }
    base.update(overrides)
    return TradeRecord(**base)


def _decision() -> Decision:
    return Decision(
        decision_id="d1",
        symbol="XAUUSDM",
        timestamp=_NOW,
        action=DecisionAction.OPEN_LONG,
        accepted=True,
        score=0.7,
        confidence=0.6,
        agreement=0.8,
        consensus=ConsensusResult(
            method="weighted_average",
            symbol="XAUUSDM",
            direction=Direction.LONG,
            score=0.7,
            agreement=0.8,
            participants=("bos", "mss"),
            contributions={"bos": 0.5, "mss": 0.2},
        ),
        confidence_breakdown={"regime": 0.3, "volatility": 0.3},
        signals_considered=("s1", "s2"),
        filters_blocking=("spread",),
        explanation=("consenso alcanzado", "régimen favorable"),
    )


def _verdicts() -> dict[str, SignalVerdict]:
    return {
        "s1": SignalVerdict(signal_id="s1", strategy="bos", r_multiple=1.5, outcome="win"),
        "s2": SignalVerdict(signal_id="s2", strategy="mss", r_multiple=0.5, outcome="win"),
    }


def _explainer(trade=None, decision=None, verdicts=None, predictor=None) -> TradeExplainer:
    return TradeExplainer(
        lambda: [trade if trade is not None else _trade()],
        lambda: [decision] if decision is not None else [_decision()],
        lambda: verdicts if verdicts is not None else _verdicts(),
        predictor,
    )


def test_unknown_trade_returns_none():
    assert _explainer().explain("nope") is None


def test_trade_can_be_found_by_position_id_too():
    assert _explainer().explain("p1") is not None


def test_entry_reports_consensus_contributions_and_session():
    found = _explainer().explain("t1")
    assert found is not None
    assert found.entry["consensus_contributions"] == {"bos": 0.5, "mss": 0.2}
    assert found.entry["conditions"]["sessions"] == ["europe", "america"]
    assert found.entry["conditions"]["regime"] == "trending"


def test_missing_decision_is_declared_not_zeroed():
    """Una decisión que ya no está en memoria no aportó 'cero': no se puede leer."""
    explainer = TradeExplainer(lambda: [_trade()], list, _verdicts, None)
    found = explainer.explain("t1")
    assert found is not None
    assert found.entry["consensus_contributions"] is None
    assert found.confirmations["status"] == "no_disponible"


def test_confirmations_list_what_passed_and_what_was_missing():
    found = _explainer().explain("t1")
    assert found is not None
    assert found.confirmations["missing"] == ["spread"]
    assert "consenso alcanzado" in found.confirmations["passed"]


def test_regime_exit_is_reported_as_thesis_unresolved():
    found = _explainer().explain("t1")
    assert found is not None
    assert found.exit["thesis_resolved"] is False
    assert found.exit["exit_regime"] == "ranging"


def test_take_profit_is_reported_as_thesis_resolved():
    found = _explainer(trade=_trade(exit_reason=ExitReason.TAKE_PROFIT)).explain("t1")
    assert found is not None
    assert found.exit["thesis_resolved"] is True


def test_signal_versus_execution_measures_the_gap():
    found = _explainer().explain("t1")
    assert found is not None
    gap = found.signal_vs_execution
    assert gap["status"] == "matched"
    assert gap["signal_r"] == 1.0  # media de 1.5 y 0.5: la decisión fue multi-estrategia
    assert gap["execution_r"] == 0.4
    assert gap["gap_r"] == -0.6
    assert "la ejecución perdió 0.60R" in found.summary


def test_trade_without_signal_ids_is_legacy_not_a_zero_gap():
    found = _explainer(trade=_trade(signal_ids=())).explain("t1")
    assert found is not None
    assert found.signal_vs_execution["status"] == "unmatched_legacy"
    assert found.signal_vs_execution["gap_r"] is None


def test_unresolved_signals_are_distinguished_from_missing_ones():
    found = _explainer(verdicts={}).explain("t1")
    assert found is not None
    assert found.signal_vs_execution["status"] == "unmatched_unresolved"
    assert found.signal_vs_execution["gap_r"] is None


def test_without_a_model_the_section_says_so():
    found = _explainer().explain("t1")
    assert found is not None
    assert found.model["status"] == "sin_modelo"


def test_model_section_reuses_the_predictor_and_survives_its_failure():
    calls: list[dict] = []

    def _ok(context):
        calls.append(dict(context))
        return {"probability": 0.61, "contributions": {"regime": 0.2}}

    found = _explainer(predictor=_ok).explain("t1")
    assert found is not None
    assert found.model["prediction"]["probability"] == 0.61
    assert calls[0]["strategy"] == "bos"

    def _boom(context):
        raise RuntimeError("modelo roto")

    broken = _explainer(predictor=_boom).explain("t1")
    assert broken is not None
    assert broken.model["status"] == "error"
    # El resto de la explicación sigue siendo utilizable.
    assert broken.signal_vs_execution["status"] == "matched"


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def test_explain_endpoint_serves_and_404s():
    from app.config.settings import Settings
    from app.core.container import Container
    from app.dashboard.api.main import create_app
    from starlette.testclient import TestClient

    settings = Settings()
    container = Container()
    container.register_instance(Settings, settings)
    container.register_instance(TradeExplainer, _explainer())
    client = TestClient(create_app(settings, container))

    ok = client.get("/api/trades/t1/explain")
    assert ok.status_code == 200
    body = ok.json()
    assert body["signal_vs_execution"]["gap_r"] == -0.6
    assert body["exit"]["thesis_resolved"] is False

    assert client.get("/api/trades/nope/explain").status_code == 404


def test_explain_endpoint_is_503_without_an_explainer():
    from app.config.settings import Settings
    from app.core.container import Container
    from app.dashboard.api.main import create_app
    from starlette.testclient import TestClient

    settings = Settings()
    container = Container()
    container.register_instance(Settings, settings)
    client = TestClient(create_app(settings, container))
    assert client.get("/api/trades/t1/explain").status_code == 503

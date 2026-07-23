"""Confidence Engine: la confianza es independiente del score."""

from app.config.settings import QuantConfidenceSettings
from app.engine.confidence import ConfidenceEngine
from app.engine.models import ConsensusResult, Direction, VolatilityState

from tests.unit.quant_helpers import make_context, make_signal


def _consensus(agreement: float = 1.0, score: float = 90.0) -> ConsensusResult:
    return ConsensusResult(
        method="weighted_average",
        symbol="BTCUSDT",
        direction=Direction.LONG,
        score=score,
        agreement=agreement,
    )


def test_healthy_context_gives_high_confidence():
    engine = ConfidenceEngine(QuantConfidenceSettings())
    signals = [
        make_signal(strategy="a", confidence=0.9),
        make_signal(strategy="b", confidence=0.8),
    ]
    context = make_context(data_quality=1.0, volatility=VolatilityState.NORMAL)

    confidence, breakdown = engine.compute(signals, _consensus(), context)

    assert confidence > 0.8
    for key in (
        "signal_confidence",
        "agreement",
        "data_quality",
        "liquidity",
        "volatility",
        "confirmation",
        "history",
    ):
        assert key in breakdown


def test_high_score_low_confidence_scenario():
    """Score alto + contexto malo => confianza baja (no operar)."""
    engine = ConfidenceEngine(QuantConfidenceSettings())
    signals = [make_signal(score=95.0, confidence=0.9, required_confirmation=True)]
    context = make_context(
        data_quality=0.0,
        spread_elevated=True,
        volume_sufficient=False,
        volatility=VolatilityState.LOW,
    )

    confidence, breakdown = engine.compute(signals, _consensus(score=95.0), context)

    assert confidence < 0.6, "score 95 pero el contexto hunde la confianza"
    assert breakdown["liquidity"] == 0.0
    assert breakdown["volatility"] == 0.6
    assert breakdown["confirmation"] == 0.5, "exige confirmación y está sola"
    assert breakdown["data_quality"] == 0.0


def test_multiple_signals_count_as_confirmation():
    engine = ConfidenceEngine(QuantConfidenceSettings())
    signals = [
        make_signal(strategy="a", required_confirmation=True),
        make_signal(strategy="b"),
    ]
    _, breakdown = engine.compute(signals, _consensus(), make_context(data_quality=1.0))
    assert breakdown["confirmation"] == 1.0


def test_history_factor_moves_confidence():
    engine = ConfidenceEngine(QuantConfidenceSettings())
    signals = [make_signal(strategy="a")]
    context = make_context(data_quality=1.0)

    baseline, _ = engine.compute(signals, _consensus(), context)
    engine.set_history_factor("a", 1.0)
    boosted, breakdown = engine.compute(signals, _consensus(), context)

    assert boosted > baseline
    assert breakdown["history"] == 1.0


def test_no_signals_zero_confidence_factors():
    engine = ConfidenceEngine(QuantConfidenceSettings())
    confidence, breakdown = engine.compute([], _consensus(agreement=0.0), make_context())
    assert breakdown["signal_confidence"] == 0.0
    assert confidence < 0.5

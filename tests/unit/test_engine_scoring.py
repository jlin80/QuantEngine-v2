"""Score Engine: normalización 0-100 y agregaciones."""

import pytest
from app.engine.models import Direction
from app.engine.scoring import ScoreEngine, clamp_score

from tests.unit.quant_helpers import make_signal


def test_clamp_score_bounds():
    assert clamp_score(-5.0) == 0.0
    assert clamp_score(150.0) == 100.0
    assert clamp_score(72.5) == 72.5


def test_weighted_global_score():
    signals = [
        make_signal(strategy="a", score=80.0),
        make_signal(strategy="b", score=60.0),
    ]
    score = ScoreEngine.weighted_global(signals, {"a": 3.0, "b": 1.0})
    assert score == pytest.approx((80 * 3 + 60 * 1) / 4)


def test_signed_global_ignores_neutral():
    signals = [
        make_signal(strategy="a", direction=Direction.LONG, score=80.0),
        make_signal(strategy="b", direction=Direction.SHORT, score=40.0),
        make_signal(strategy="c", direction=Direction.NEUTRAL, score=100.0),
    ]
    value = ScoreEngine.signed_global(signals, {})
    assert value == pytest.approx((80 - 40) / 2)


def test_empty_inputs_score_zero():
    assert ScoreEngine.weighted_global([], {}) == 0.0
    assert ScoreEngine.signed_global([], {}) == 0.0

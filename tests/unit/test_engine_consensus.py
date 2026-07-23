"""Motor de consenso: los 5 algoritmos y el engine intercambiable."""

import pytest
from app.config.settings import QuantConsensusSettings
from app.core.exceptions import ConfigurationError
from app.engine.consensus import (
    ConsensusEngine,
    DynamicWeighting,
    MajorityVoting,
    RegimeWeighting,
    WeightedAverage,
    WeightedVoting,
)
from app.engine.models import Direction, Regime, RegimeState
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_context, make_signal


def test_majority_voting_wins_by_count():
    result = MajorityVoting().build(
        "BTCUSDT",
        [
            make_signal(strategy="a", direction=Direction.LONG, score=80.0),
            make_signal(strategy="b", direction=Direction.LONG, score=60.0),
            make_signal(strategy="c", direction=Direction.SHORT, score=95.0),
        ],
        {},
    )
    assert result.direction is Direction.LONG
    assert result.agreement == pytest.approx(2 / 3, abs=1e-3)
    assert result.score == pytest.approx(70.0)
    assert set(result.participants) == {"a", "b", "c"}


def test_majority_voting_tie_is_neutral():
    result = MajorityVoting().build(
        "BTCUSDT",
        [
            make_signal(strategy="a", direction=Direction.LONG),
            make_signal(strategy="b", direction=Direction.SHORT),
        ],
        {},
    )
    assert result.direction is Direction.NEUTRAL


def test_neutral_signals_do_not_vote():
    result = MajorityVoting().build("BTCUSDT", [make_signal(direction=Direction.NEUTRAL)], {})
    assert result.direction is Direction.NEUTRAL
    assert result.reasons == ("sin señales direccionales",)


def test_weighted_voting_weight_beats_count():
    signals = [
        make_signal(strategy="whale", direction=Direction.LONG, score=80.0),
        make_signal(strategy="fish1", direction=Direction.SHORT, score=80.0),
        make_signal(strategy="fish2", direction=Direction.SHORT, score=80.0),
    ]
    result = WeightedVoting().build("BTCUSDT", signals, {"whale": 3.0, "fish1": 1.0, "fish2": 1.0})
    assert result.direction is Direction.LONG
    assert result.agreement == pytest.approx(3 / 5)


def test_weighted_average_is_signed():
    signals = [
        make_signal(strategy="a", direction=Direction.LONG, score=80.0),
        make_signal(strategy="b", direction=Direction.SHORT, score=60.0),
    ]
    result = WeightedAverage().build("BTCUSDT", signals, {})
    assert result.direction is Direction.LONG
    assert result.score == pytest.approx(10.0)  # (80 - 60) / 2


def test_dynamic_weighting_boosts_recent_performance():
    factors = {"hot": 1.0, "cold": 0.0}
    algo = DynamicWeighting(lambda name: factors[name])
    signals = [
        make_signal(strategy="hot", direction=Direction.LONG, score=80.0),
        make_signal(strategy="cold", direction=Direction.SHORT, score=80.0),
    ]
    result = algo.build("BTCUSDT", signals, {"hot": 1.0, "cold": 1.0})
    assert result.direction is Direction.LONG, "el rendimiento reciente inclina la balanza"


def test_regime_weighting_uses_multipliers():
    algo = RegimeWeighting({"trending": {"trend_follower": 3.0, "mean_rev": 0.1}})
    signals = [
        make_signal(strategy="trend_follower", direction=Direction.LONG, score=80.0),
        make_signal(strategy="mean_rev", direction=Direction.SHORT, score=90.0),
    ]
    context = make_context(
        regime=RegimeState(symbol="BTCUSDT", primary=Regime.TRENDING, detected_at=utc_now())
    )
    with_regime = algo.build("BTCUSDT", signals, {}, context)
    assert with_regime.direction is Direction.LONG

    without_context = algo.build("BTCUSDT", signals, {})
    assert without_context.direction is Direction.SHORT, "sin régimen ganaría el score mayor"


def test_consensus_engine_swaps_algorithms():
    engine = ConsensusEngine(QuantConsensusSettings(method="weighted_average"))
    assert engine.method == "weighted_average"
    assert len(engine.available_methods) == 5

    engine.set_method("majority_voting")
    assert engine.method == "majority_voting"

    with pytest.raises(ConfigurationError):
        engine.set_method("coin_flip")


def test_consensus_engine_rejects_unknown_method_in_config():
    with pytest.raises(ConfigurationError):
        ConsensusEngine(QuantConsensusSettings(method="astrology"))


def test_consensus_engine_weights_are_configurable():
    engine = ConsensusEngine(QuantConsensusSettings(method="weighted_voting"), {"a": 1.0, "b": 1.0})
    signals = [
        make_signal(strategy="a", direction=Direction.LONG, score=80.0),
        make_signal(strategy="b", direction=Direction.SHORT, score=80.0),
    ]
    assert engine.build("BTCUSDT", signals).direction is Direction.NEUTRAL

    engine.set_weight("a", 5.0)
    assert engine.build("BTCUSDT", signals).direction is Direction.LONG
    assert engine.weights["a"] == 5.0

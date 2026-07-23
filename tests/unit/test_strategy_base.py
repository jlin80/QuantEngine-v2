"""QuantStrategy: pipeline de score, confianza, confirmaciones y explicación."""

from collections.abc import Sequence
from typing import Any, ClassVar

import pytest
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction, EntryZone, VolatilityState
from app.engine.validators import SignalValidator
from app.market.models import Candle
from app.market.services import MarketDataService
from app.strategies.base import Assessment, QuantStrategy
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_candles, make_context, make_market, make_trade


class DummyStrategy(QuantStrategy):
    name = "dummy"
    version = "1.0"
    category: ClassVar[str] = "test"
    preferred_regimes: ClassVar[tuple[str, ...]] = ()
    default_parameters: ClassVar[dict[str, Any]] = {
        "min_candles": 5,
        "lookback": 30,
        "confirmations": ["spread"],
        "custom_knob": 7,
    }

    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        price = candles[-1].close
        return Assessment(
            direction=Direction.LONG,
            quality=0.8,
            strength=0.6,
            context_fit=0.7,
            probability=0.6,
            risk=0.2,
            reasons=["Setup de prueba detectado."],
            entry=EntryZone(low=price - 0.1, high=price + 0.1),
            stop_loss=price - 1.0,
            take_profit=price + 2.0,
        )


def _ctx(market: MarketDataService, **overrides: Any) -> AnalysisContext:
    from app.engine.feature_store import FeatureStore

    return AnalysisContext(
        symbol="BTCUSDT",
        fired_at=utc_now(),
        trigger="test",
        market=market,
        features=FeatureStore(market),
        context=make_context(**{"data_quality": 1.0, **overrides}),
    )


def _market() -> MarketDataService:
    return make_market(candles=make_candles([100.0] * 40), trades=[make_trade(price=100.0)])


def test_parameter_layering_core_default_user():
    strategy = DummyStrategy({"custom_knob": 99, "min_signal_score": 10.0})
    assert strategy.parameters["custom_knob"] == 99  # usuario gana
    assert strategy.parameters["min_candles"] == 5  # default de la subclase
    assert strategy.parameters["atr_period"] == 14  # core de la base
    assert strategy.fparam("min_signal_score") == 10.0


async def test_full_signal_structure():
    strategy = DummyStrategy()
    signal = await strategy.analyze(_ctx(_market()))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert 0 < signal.score <= 100
    assert 0 < signal.confidence <= 1
    assert signal.entry_zone is not None and signal.stop_loss is not None
    assert signal.risk_reward == pytest.approx(2.0)
    assert signal.expiration is not None
    assert SignalValidator().validate(signal, now=utc_now()) == []
    assert signal.metadata["category"] == "test"
    assert set(signal.metadata["score_components"]) == {
        "quality",
        "strength",
        "context",
        "probability",
        "risk",
    }


async def test_missing_confirmation_marks_signal():
    strategy = DummyStrategy()
    ctx = _ctx(_market(), spread_elevated=True, spread_bps=25.0)
    signal = await strategy.analyze(ctx)
    assert signal is not None
    assert signal.required_confirmation is True
    assert any("Falta confirmación de spread" in w for w in signal.warnings)


async def test_passed_confirmation_lands_in_reasons():
    strategy = DummyStrategy()
    signal = await strategy.analyze(_ctx(_market(), spread_elevated=False))
    assert signal is not None
    assert signal.required_confirmation is False
    assert any("Confirmado mediante spread" in r for r in signal.reasons)


async def test_score_weights_are_configurable():
    neutral = DummyStrategy()
    only_quality = DummyStrategy(
        {
            "score_weights": {
                "quality": 1.0,
                "strength": 0.0,
                "context": 0.0,
                "probability": 0.0,
                "risk": 0.0,
            }
        }
    )
    base_signal = await neutral.analyze(_ctx(_market()))
    quality_signal = await only_quality.analyze(_ctx(_market()))
    assert base_signal is not None and quality_signal is not None
    assert quality_signal.score == pytest.approx(80.0)  # quality=0.8 → 80
    assert quality_signal.score != base_signal.score


async def test_min_score_gate_and_explanation():
    strategy = DummyStrategy({"min_signal_score": 99.0})
    signal = await strategy.analyze(_ctx(_market()))
    assert signal is None
    assert "setup débil" in strategy.explain()


async def test_insufficient_data_explanation():
    strategy = DummyStrategy({"min_candles": 100})
    signal = await strategy.analyze(_ctx(_market()))
    assert signal is None
    assert "datos insuficientes" in strategy.explain()


async def test_confidence_reacts_to_context():
    strategy = DummyStrategy()
    good = await strategy.analyze(_ctx(_market(), volume_sufficient=True))
    bad = await strategy.analyze(
        _ctx(
            _market(),
            volume_sufficient=False,
            spread_elevated=True,
            volatility=VolatilityState.LOW,
            data_quality=0.2,
        )
    )
    assert good is not None and bad is not None
    assert bad.confidence < good.confidence


async def test_determinism_same_input_same_signal():
    strategy = DummyStrategy()
    market = _market()
    fired = utc_now()
    from app.engine.feature_store import FeatureStore

    features = FeatureStore(market)
    context = make_context(data_quality=1.0)

    def ctx() -> AnalysisContext:
        return AnalysisContext(
            symbol="BTCUSDT",
            fired_at=fired,
            trigger="test",
            market=market,
            features=features,
            context=context,
        )

    first = await strategy.analyze(ctx())
    second = await strategy.analyze(ctx())
    assert first is not None and second is not None
    assert first.score == second.score
    assert first.confidence == second.confidence
    assert first.reasons == second.reasons

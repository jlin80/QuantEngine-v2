"""Filtros independientes: cada uno puede vetar, siempre con razón."""

from datetime import timedelta

from app.config.settings import QuantFiltersSettings
from app.engine.filters import (
    CorrelationFilter,
    DrawdownFilter,
    FilterChain,
    LiquidityFilter,
    NewsFilter,
    SessionFilter,
    SpreadFilter,
    VolatilityFilter,
    build_filter_chain,
)
from app.engine.models import (
    ConsensusResult,
    Decision,
    DecisionAction,
    Direction,
    VolatilityState,
)
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_context

CONSENSUS = ConsensusResult(
    method="weighted_average",
    symbol="BTCUSDT",
    direction=Direction.LONG,
    score=80.0,
    agreement=1.0,
)


def test_session_filter():
    ctx = make_context(sessions=("asia",))
    blocked = SessionFilter(["europe"]).check(ctx, CONSENSUS)
    assert blocked.passed is False
    assert "sesión" in blocked.reason

    assert SessionFilter(["asia", "europe"]).check(ctx, CONSENSUS).passed is True


def test_session_filter_exempts_always_open_symbols():
    """Cripto (24/7) no debe bloquearse aunque no haya sesión forex activa."""
    ctx = make_context(symbol="ETHUSDM", sessions=())
    blocked = SessionFilter(["europe"]).check(ctx, CONSENSUS)
    assert blocked.passed is False

    exempt = SessionFilter(["europe"], always_open=["ETHUSDM"]).check(ctx, CONSENSUS)
    assert exempt.passed is True


def test_spread_filter():
    blocked = SpreadFilter().check(make_context(spread_elevated=True, spread_bps=12.5), CONSENSUS)
    assert blocked.passed is False
    assert "12.5" in blocked.reason
    assert SpreadFilter().check(make_context(spread_elevated=False), CONSENSUS).passed is True


def test_volatility_filter():
    blocked = VolatilityFilter().check(make_context(volatility=VolatilityState.LOW), CONSENSUS)
    assert blocked.passed is False
    ok = VolatilityFilter().check(make_context(volatility=VolatilityState.NORMAL), CONSENSUS)
    assert ok.passed is True


def test_liquidity_filter():
    blocked = LiquidityFilter().check(
        make_context(volume_sufficient=False, volume_recent=1.0), CONSENSUS
    )
    assert blocked.passed is False
    assert LiquidityFilter().check(make_context(volume_sufficient=True), CONSENSUS).passed


def test_news_filter():
    assert NewsFilter().check(make_context(news_blackout=True), CONSENSUS).passed is False
    assert NewsFilter().check(make_context(news_blackout=False), CONSENSUS).passed is True


def test_drawdown_filter():
    blocked = DrawdownFilter(5.0, lambda: 6.5).check(make_context(), CONSENSUS)
    assert blocked.passed is False
    assert "6.50%" in blocked.reason
    assert DrawdownFilter(5.0, lambda: 2.0).check(make_context(), CONSENSUS).passed is True


def _decision(symbol: str, *, age_seconds: float = 0.0, accepted: bool = True) -> Decision:
    return Decision(
        symbol=symbol,
        timestamp=utc_now() - timedelta(seconds=age_seconds),
        action=DecisionAction.OPEN_LONG,
        accepted=accepted,
        score=80.0,
        confidence=0.8,
        agreement=1.0,
    )


def test_correlation_filter_blocks_recent_group_decision():
    recent = [_decision("ETHUSDT")]
    filt = CorrelationFilter([["BTCUSDT", "ETHUSDT"]], 30.0, lambda: recent)

    blocked = filt.check(make_context(symbol="BTCUSDT"), CONSENSUS)
    assert blocked.passed is False
    assert "ETHUSDT" in blocked.reason


def test_correlation_filter_ignores_old_or_foreign_decisions():
    old = [_decision("ETHUSDT", age_seconds=3600)]
    filt = CorrelationFilter([["BTCUSDT", "ETHUSDT"]], 30.0, lambda: old)
    assert filt.check(make_context(symbol="BTCUSDT"), CONSENSUS).passed is True

    foreign = [_decision("XAUUSD")]
    filt2 = CorrelationFilter([["BTCUSDT", "ETHUSDT"]], 30.0, lambda: foreign)
    assert filt2.check(make_context(symbol="BTCUSDT"), CONSENSUS).passed is True

    rejected = [_decision("ETHUSDT", accepted=False)]
    filt3 = CorrelationFilter([["BTCUSDT", "ETHUSDT"]], 30.0, lambda: rejected)
    assert filt3.check(make_context(symbol="BTCUSDT"), CONSENSUS).passed is True


def test_chain_evaluates_every_filter_for_explainability():
    """La cadena no se corta en el primer bloqueo: reporta TODOS."""
    ctx = make_context(
        sessions=(), spread_elevated=True, volatility=VolatilityState.LOW, news_blackout=True
    )
    chain = FilterChain([SessionFilter(["asia"]), SpreadFilter(), VolatilityFilter(), NewsFilter()])

    results = chain.evaluate(ctx, CONSENSUS)

    assert len(results) == 4
    assert all(result.passed is False for result in results)
    assert all(result.reason for result in results), "todo bloqueo lleva razón"


def test_build_filter_chain_respects_enabled_list():
    settings = QuantFiltersSettings(enabled=["spread", "news"])
    chain = build_filter_chain(settings, drawdown_reader=lambda: 0.0, recent_decisions=lambda: [])
    assert chain.names == ["spread", "news"]

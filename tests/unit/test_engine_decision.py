"""Decision Engine: una única decisión, siempre explicable."""

import asyncio

from app.config.settings import (
    QuantConfidenceSettings,
    QuantConsensusSettings,
    QuantContextSettings,
    QuantRegimeSettings,
)
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.decision_engine import DecisionEngine
from app.engine.feature_store import FeatureStore
from app.engine.filters import FilterChain, SpreadFilter
from app.engine.market_context import MarketContextEngine
from app.engine.models import DecisionAction, Direction, SignalStatus
from app.engine.regime_detection import RegimeDetector
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import SignalHistoryStore
from app.engine.validators import SignalValidator
from app.market.services import MarketDataService

from tests.unit.quant_helpers import (
    make_candles,
    make_market,
    make_signal,
    make_ticker,
    make_trade,
)


def _stack(
    market: MarketDataService,
    consensus_settings: QuantConsensusSettings,
    filters: FilterChain,
    bus: EventBus | None = None,
) -> tuple[DecisionEngine, SignalEngine, SignalHistoryStore]:
    history = SignalHistoryStore()
    signals = SignalEngine(SignalValidator(), history, bus)
    features = FeatureStore(market)
    regime = RegimeDetector(market, QuantRegimeSettings())
    context = MarketContextEngine(market, features, regime, QuantContextSettings())
    consensus = ConsensusEngine(consensus_settings)
    confidence = ConfidenceEngine(QuantConfidenceSettings())
    decision = DecisionEngine(
        signals, context, consensus, confidence, filters, history, consensus_settings, bus
    )
    return decision, signals, history


def _healthy_market() -> MarketDataService:
    return make_market(
        candles=make_candles([100.0, 101.0] * 25),
        ticker=make_ticker(bid=100.0, ask=100.01),  # ~1 bps: spread sano
        trades=[make_trade(price=100.5)],
    )


async def test_decision_accepted_when_everything_passes():
    settings = QuantConsensusSettings(
        method="weighted_average",
        min_signals=2,
        min_score=60.0,
        min_confidence=0.5,
        min_agreement=0.5,
    )
    decision_engine, signals, history = _stack(_healthy_market(), settings, FilterChain([]))
    await signals.submit(make_signal(strategy="alpha", score=80.0))
    await signals.submit(make_signal(strategy="beta", score=90.0))

    decision = await decision_engine.evaluate("BTCUSDT")

    assert decision.accepted is True
    assert decision.action is DecisionAction.OPEN_LONG
    assert decision.score == 85.0
    assert decision.agreement == 1.0
    assert any("aceptada" in line for line in decision.explanation)
    # Las señales quedan resueltas y el historial completo.
    assert signals.active_signals("BTCUSDT") == []
    assert len(history.signals(status=SignalStatus.ACCEPTED)) == 2
    assert history.decisions()[-1].decision_id == decision.decision_id


async def test_rejection_is_fully_explained():
    """Nunca 'no operar' a secas: cada causa aparece en la explicación."""
    settings = QuantConsensusSettings(min_signals=2, min_score=60.0)
    market = make_market(
        candles=make_candles([100.0, 101.0] * 25),
        ticker=make_ticker(bid=100.0, ask=100.2),  # ~20 bps: spread elevado
        trades=[make_trade(price=100.5)],
    )
    decision_engine, signals, history = _stack(market, settings, FilterChain([SpreadFilter()]))
    await signals.submit(make_signal(direction=Direction.SHORT, score=40.0))

    decision = await decision_engine.evaluate("BTCUSDT")

    assert decision.accepted is False
    assert decision.action is DecisionAction.STAND_ASIDE
    assert "spread" in decision.filters_blocking
    text = " ".join(decision.explanation)
    assert "No se opera porque:" in text
    assert "Señales insuficientes" in text
    assert "Score global" in text
    assert "spread" in text
    assert len(history.signals(status=SignalStatus.REJECTED)) == 1


async def test_no_signals_stand_aside():
    decision_engine, _, history = _stack(
        _healthy_market(), QuantConsensusSettings(), FilterChain([])
    )
    decision = await decision_engine.evaluate("BTCUSDT")
    assert decision.accepted is False
    assert decision.action is DecisionAction.STAND_ASIDE
    assert "No hay señales activas" in decision.explanation[0]
    assert history.decisions(), "hasta el stand-aside queda registrado"


async def test_conflicting_directions_are_flagged():
    settings = QuantConsensusSettings(min_signals=1, min_score=0.0, min_confidence=0.0)
    decision_engine, signals, _ = _stack(_healthy_market(), settings, FilterChain([]))
    await signals.submit(make_signal(strategy="alpha", direction=Direction.LONG, score=80.0))
    await signals.submit(make_signal(strategy="beta", direction=Direction.SHORT, score=70.0))

    decision = await decision_engine.evaluate("BTCUSDT")

    assert any("Conflicto" in line for line in decision.explanation)


async def test_events_are_published():
    bus = EventBus()
    await bus.start()
    seen: list[str] = []

    async def collector(event: Event) -> None:
        seen.append(event.name)

    bus.subscribe(collector)
    settings = QuantConsensusSettings(min_signals=1, min_score=0.0, min_confidence=0.0)
    market = make_market(
        candles=make_candles([100.0, 101.0] * 25),
        ticker=make_ticker(bid=100.0, ask=100.2),  # dispara el filtro de spread
        trades=[make_trade(price=100.5)],
    )
    decision_engine, signals, _ = _stack(market, settings, FilterChain([SpreadFilter()]), bus)
    await signals.submit(make_signal(score=90.0))

    await decision_engine.evaluate("BTCUSDT")
    await asyncio.sleep(0.05)
    await bus.stop()

    assert "SignalCreated" in seen
    assert "ConsensusReached" in seen
    assert "FilterTriggered" in seen
    assert "DecisionGenerated" in seen

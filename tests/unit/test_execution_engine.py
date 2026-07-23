"""Execution Engine: flujo completo de paper trading, extremo a extremo."""

import asyncio
from datetime import timedelta

from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.engine.events import DecisionGenerated
from app.execution.execution_engine.engine import _MarketView
from app.execution.models import ExitReason
from app.market.services import MarketDataService, MarketStateStore

from tests.unit.execution_helpers import (
    make_engine,
    make_execution_settings,
    make_market_with_state,
)
from tests.unit.quant_helpers import make_candles, make_ticker


def _decision(action: str = "open_long", accepted: bool = True) -> DecisionGenerated:
    return DecisionGenerated(
        source="test",
        decision_id="d1",
        symbol="BTCUSDT",
        action=action,
        accepted=accepted,
        score=80.0,
        confidence=0.8,
        summary="momentum",
    )


def _market() -> tuple[MarketDataService, MarketStateStore]:
    return make_market_with_state(
        candles=make_candles([100.0, 101.0, 100.5, 101.5, 102.0] * 6),
        ticker=make_ticker(bid=101.9, ask=101.95),
    )


async def test_accepted_decision_opens_a_position():
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    assert position.side.value == "long"
    assert position.stop_loss is not None and position.take_profit is not None
    assert len(engine.positions.open_positions) == 1
    assert engine.orders.status()["filled"] == 1
    # La comisión de apertura ya salió de la caja.
    assert engine.portfolio.balance < engine.portfolio.initial_balance


async def test_rejected_decision_does_nothing():
    market, _ = _market()
    engine = make_engine(market)
    assert await engine.process_decision(_decision(accepted=False)) is None
    assert not engine.positions.open_positions


async def test_spread_filter_blocks_entry():
    market, _ = make_market_with_state(
        candles=make_candles([100.0, 101.0] * 15),
        ticker=make_ticker(bid=100.0, ask=100.5),  # ~50 bps: demasiado ancho
    )
    engine = make_engine(market, make_execution_settings(risk={"max_spread_bps": 5.0}))
    assert await engine.process_decision(_decision()) is None
    assert not engine.positions.open_positions


async def test_take_profit_closes_and_journals():
    market, state = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    # El mercado salta muy por encima del objetivo → salida por take profit.
    state.update_ticker(make_ticker(bid=500.0, ask=500.05))
    await engine.manage_once()
    assert not engine.positions.open_positions
    assert engine.journal.count == 1
    trade = engine.journal.all()[0]
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.pnl > 0
    assert engine.portfolio.total_trades == 1


async def test_time_exit_closes_position():
    market, _ = _market()
    engine = make_engine(market, make_execution_settings(max_holding_minutes=0.0))
    position = await engine.process_decision(_decision())
    assert position is not None
    # max_holding_minutes=0 desactiva la salida por tiempo; la forzamos manual.
    await engine.close_position(position, ExitReason.MANUAL)
    assert engine.journal.count == 1


async def test_manual_break_even_moves_stop():
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    assert await engine.move_break_even(position.position_id)
    assert position.stop_loss == position.entry_price and position.break_even_active


async def test_full_flow_publishes_events():
    bus = EventBus()
    await bus.start()
    seen: list[str] = []

    async def collector(event: Event) -> None:
        seen.append(event.name)

    bus.subscribe(collector)
    market, state = _market()
    engine = make_engine(market)
    engine._bus = bus  # inyecta el bus para publicar eventos
    await engine.process_decision(_decision())
    state.update_ticker(make_ticker(bid=500.0, ask=500.05))
    await engine.manage_once()
    await asyncio.sleep(0.05)
    await bus.stop()

    assert "OrderCreated" in seen
    assert "OrderExecuted" in seen
    assert "PositionOpened" in seen
    assert "PositionClosed" in seen


async def test_regime_change_exit_respects_min_holding_seconds():
    """Un régimen que 'parpadea' justo al abrir no debe cortar la posición
    de inmediato — solo tras el tiempo mínimo de retención configurado."""
    market, _ = _market()
    engine = make_engine(market, make_execution_settings(regime_change_min_holding_seconds=120.0))
    position = await engine.process_decision(_decision())
    assert position is not None
    position.regime = "trending"
    engine._context = object()  # type: ignore[assignment]  # activa la rama de salida por régimen

    async def fake_view(symbol: str) -> _MarketView:
        return _MarketView(
            atr=None,
            atr_pct=None,
            spread_bps=None,
            regime="ranging",
            volatility="normal",
            volume=None,
            last_price=None,
            session="america",
        )

    engine._market_view = fake_view  # type: ignore[method-assign]

    assert await engine._exit_reason(position) is None  # recién abierta

    position.opened_at = position.opened_at - timedelta(seconds=200)
    assert await engine._exit_reason(position) is ExitReason.REGIME_CHANGE


async def test_reconciliation_settles_position_closed_outside_the_bot():
    """Si el usuario cierra la posición a mano en el broker, el bot lo detecta."""
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    position.metadata["broker_ref"] = "12345"

    class _BrokerWithoutThatTicket:
        def open_position_tickets(self, symbol: str) -> set[int]:
            return set()  # el broker real ya no tiene ninguna posición abierta

    engine._paper.open_position_tickets = _BrokerWithoutThatTicket().open_position_tickets  # type: ignore[attr-defined]

    await engine.manage_once()

    assert not engine.positions.open_positions
    assert engine.journal.count == 1
    trade = engine.journal.all()[0]
    assert trade.exit_reason is ExitReason.MANUAL


async def test_stop_distance_never_narrower_than_the_configured_floor():
    """Un ATR subestimado (velas casi planas) no debe dar un stop más angosto
    que el spread real — sin el piso, el stop se dispara por ruido al entrar."""
    market, _ = make_market_with_state(
        candles=make_candles([1880.0, 1880.05, 1879.98, 1880.02, 1880.0] * 6),
        ticker=make_ticker(bid=1881.0, ask=1881.05),
    )
    engine = make_engine(market, make_execution_settings(sizing={"min_stop_pct": 0.15}))
    position = await engine.process_decision(_decision())
    assert position is not None
    assert position.stop_loss is not None
    distance = abs(position.entry_price - position.stop_loss)
    floor = position.entry_price * (0.15 / 100.0)
    assert distance >= floor - 1e-9


async def test_reconciliation_leaves_matching_positions_alone():
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    position.metadata["broker_ref"] = "12345"

    class _BrokerWithThatTicket:
        def open_position_tickets(self, symbol: str) -> set[int]:
            return {12345}

    engine._paper.open_position_tickets = _BrokerWithThatTicket().open_position_tickets  # type: ignore[attr-defined]

    await engine.manage_once()

    assert engine.positions.open_positions  # sigue abierta, no se tocó

"""Execution Engine: flujo completo de paper trading, extremo a extremo."""

import asyncio
from datetime import timedelta

import pytest
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


async def test_symbol_toggle_blocks_entry():
    """Un símbolo con el toggle en false no abre posiciones."""
    market, _ = _market()
    engine = make_engine(market, make_execution_settings(symbols_enabled={"BTCUSDT": False}))

    assert await engine.process_decision(_decision()) is None
    assert not engine.positions.open_positions


async def test_symbol_toggle_defaults_to_enabled():
    """Lo no listado en el toggle se sigue operando con normalidad."""
    market, _ = _market()
    engine = make_engine(market, make_execution_settings(symbols_enabled={"XAUUSDM": False}))

    assert await engine.process_decision(_decision()) is not None


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
    # Ya pasó el mínimo, pero se exigen 2 confirmaciones consecutivas.
    assert await engine._exit_reason(position) is None
    assert await engine._exit_reason(position) is ExitReason.REGIME_CHANGE


def _regime_view(regime: str):
    async def fake_view(symbol: str) -> _MarketView:
        return _MarketView(
            atr=None,
            atr_pct=None,
            spread_bps=None,
            regime=regime,
            volatility="normal",
            volume=None,
            last_price=None,
            session="america",
        )

    return fake_view


async def _aged_position(engine, entry_regime: str):
    position = await engine.process_decision(_decision())
    assert position is not None
    position.regime = entry_regime
    position.opened_at = position.opened_at - timedelta(seconds=600)
    engine._context = object()  # type: ignore[assignment]
    return position


async def test_regime_exit_ignores_changes_inside_the_same_family():
    """`breakout`→`trending` es la misma tesis direccional: no debe cerrar.

    Era la causa del churn: el 80% de las salidas eran por régimen sin llegar
    nunca al stop ni al objetivo, porque cualquier cambio de etiqueta valía.
    """
    market, _ = _market()
    engine = make_engine(market)
    position = await _aged_position(engine, "breakout")
    engine._market_view = _regime_view("trending")  # type: ignore[method-assign]

    for _ in range(5):
        assert await engine._exit_reason(position) is None


async def test_regime_exit_fires_when_family_changes_and_is_confirmed():
    """`trending`→`reversal` sí cambia de familia: cierra tras confirmarse."""
    market, _ = _market()
    engine = make_engine(market)
    position = await _aged_position(engine, "trending")
    engine._market_view = _regime_view("reversal")  # type: ignore[method-assign]

    assert await engine._exit_reason(position) is None  # 1/2
    assert await engine._exit_reason(position) is ExitReason.REGIME_CHANGE  # 2/2


async def test_regime_adverse_streak_resets_when_regime_returns():
    """Una lectura adversa aislada no debe acumularse con otra posterior."""
    market, _ = _market()
    engine = make_engine(market)
    position = await _aged_position(engine, "trending")

    engine._market_view = _regime_view("reversal")  # type: ignore[method-assign]
    assert await engine._exit_reason(position) is None  # racha = 1

    engine._market_view = _regime_view("breakout")  # type: ignore[method-assign]
    assert await engine._exit_reason(position) is None  # misma familia: reinicia
    assert position.metadata["regime_adverse_streak"] == 0

    engine._market_view = _regime_view("reversal")  # type: ignore[method-assign]
    assert await engine._exit_reason(position) is None  # vuelve a empezar en 1


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


async def test_syncs_portfolio_balance_from_real_broker():
    """En demo, el balance del Portfolio Manager debe seguir a la cuenta real."""
    market, _ = _market()
    engine = make_engine(market)

    real_balance = {"value": 954.22}

    def account_balance() -> float:
        return real_balance["value"]

    engine._paper.account_balance = account_balance  # type: ignore[attr-defined]

    await engine.manage_once()
    # Primera sync: fija baseline → return 0 sobre el saldo real, no sobre config.
    assert engine.portfolio.balance == 954.22
    assert engine.portfolio.initial_balance == 954.22
    assert engine.portfolio.realized_pnl == 0.0

    # Un cambio posterior de la cuenta se refleja sin re-fijar la línea base.
    real_balance["value"] = 968.21
    await engine.manage_once()
    assert engine.portfolio.balance == 968.21
    assert engine.portfolio.initial_balance == 954.22
    assert round(engine.portfolio.realized_pnl, 2) == 13.99


async def test_paper_broker_leaves_portfolio_balance_untouched():
    """Sin account_balance (paper broker), no se sincroniza nada."""
    market, _ = _market()
    engine = make_engine(market)
    before = engine.portfolio.balance
    await engine.manage_once()
    assert engine.portfolio.balance == before


class _BrokerWithLivePositions:
    """Paper broker + las capacidades opcionales de un broker real (MT5)."""

    def __init__(self, inner, live):
        self._inner = inner
        self._live = live

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def open_broker_positions(self):
        return list(self._live)


async def test_adopts_broker_positions_on_start():
    """Tras un reinicio, lo que ya esta vivo en el broker debe adoptarse."""
    from app.execution.models import BrokerPosition, PositionSide

    market, _ = _market()
    engine = make_engine(market)
    engine._paper = _BrokerWithLivePositions(
        engine._paper,
        [
            BrokerPosition(
                ticket=584945506,
                symbol="USTECM",
                is_long=True,
                volume=0.01,
                price_open=28073.69,
                stop_loss=28031.58,
                take_profit=28136.86,
            )
        ],
    )

    await engine._adopt_broker_positions()

    open_positions = engine.positions.open_positions
    assert len(open_positions) == 1
    adopted = open_positions[0]
    assert adopted.symbol == "USTECM"
    assert adopted.side is PositionSide.LONG
    assert adopted.quantity == 0.01
    assert adopted.stop_loss == 28031.58
    assert adopted.initial_stop == 28031.58  # base para medir R
    assert adopted.metadata["broker_ref"] == "584945506"
    assert adopted.metadata["adopted"] is True


async def test_adoption_is_idempotent():
    """Adoptar dos veces no duplica: el ticket ya rastreado se ignora."""
    from app.execution.models import BrokerPosition

    market, _ = _market()
    engine = make_engine(market)
    live = [
        BrokerPosition(
            ticket=999,
            symbol="ETHUSDM",
            is_long=False,
            volume=0.27,
            price_open=1946.24,
            stop_loss=1949.21,
            take_profit=1941.91,
        )
    ]
    engine._paper = _BrokerWithLivePositions(engine._paper, live)

    await engine._adopt_broker_positions()
    await engine._adopt_broker_positions()

    assert len(engine.positions.open_positions) == 1


async def test_paper_broker_has_nothing_to_adopt():
    """Con el paper broker (sin la capacidad) la adopcion no hace nada."""
    market, _ = _market()
    engine = make_engine(market)

    await engine._adopt_broker_positions()

    assert not engine.positions.open_positions


def _view(spread_bps: float | None, atr: float | None = 0.5) -> _MarketView:
    """ATR pequeño por defecto: así mandan los PISOS, que es lo que se prueba."""
    return _MarketView(
        atr=atr,
        atr_pct=None,
        spread_bps=spread_bps,
        regime="ranging",
        volatility="normal",
        volume=None,
        last_price=None,
        session="america",
    )


async def test_stop_floor_widens_for_wide_spread_symbols():
    """ETH (spread ~3 bps) necesita un stop mas ancho que el 0.15% global.

    Con 0.15% el stop quedaba a solo 5x el spread y lo barria el ruido: las
    operaciones que morian antes de 180s perdian -$14.23 mientras las que
    sobrevivian ganaban +$2.75.
    """
    market, _ = _market()
    engine = make_engine(market)

    price = 1880.0
    pct_floor = price * 0.15 / 100  # 2.82
    stop = engine._stop_distance(_view(spread_bps=3.03), price)

    # 3.03 bps x 8 = 24.24 bps = 0.242% -> 4.556, mas ancho que el 0.15%.
    assert stop > pct_floor
    assert stop == pytest.approx(price * (3.03 / 10_000) * 8.0, rel=1e-6)


async def test_stop_floor_unchanged_for_tight_spread_symbols():
    """Oro (0.56 bps) y USTEC (1.05 bps) ya tenian margen: no deben cambiar."""
    market, _ = _market()
    engine = make_engine(market)

    for price, spread in ((4080.0, 0.56), (28060.0, 1.05)):
        pct_floor = price * 0.15 / 100
        assert engine._stop_distance(_view(spread_bps=spread), price) == pytest.approx(pct_floor)


async def test_stop_floor_ignores_missing_spread():
    """Sin spread conocido se cae al piso porcentual, sin romperse."""
    market, _ = _market()
    engine = make_engine(market)

    price = 1880.0
    assert engine._stop_distance(_view(spread_bps=None), price) == pytest.approx(price * 0.15 / 100)


async def test_atr_still_wins_when_wider_than_both_floors():
    """El ATR sigue mandando si es mas ancho que los dos pisos."""
    market, _ = _market()
    engine = make_engine(market)

    price = 1880.0
    stop = engine._stop_distance(_view(spread_bps=3.03, atr=20.0), price)

    assert stop == pytest.approx(20.0 * 1.5)  # atr_stop_multiplier por defecto


async def test_external_close_at_stop_is_labelled_stop_loss():
    """El broker ejecuta el SL que dejo el bot: no es una salida 'manual'.

    Registrarlas todas como MANUAL hacia que las estadisticas dijeran
    "0 stop losses" mientras el broker ejecutaba decenas (50 en un dia).
    """
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    assert position.stop_loss is not None

    reason, detail = engine._infer_external_exit(position, position.stop_loss)

    assert reason is ExitReason.STOP_LOSS
    assert "stop" in detail


async def test_external_close_at_target_is_labelled_take_profit():
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    assert position.take_profit is not None

    reason, _ = engine._infer_external_exit(position, position.take_profit)

    assert reason is ExitReason.TAKE_PROFIT


async def test_external_close_far_from_levels_stays_manual():
    """Lejos del stop y del objetivo si es un cierre a mano del operador."""
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None

    midpoint = (position.entry_price + (position.take_profit or 0)) / 2
    reason, detail = engine._infer_external_exit(position, midpoint)

    assert reason is ExitReason.MANUAL
    assert "fuera del bot" in detail


async def test_external_close_tolerates_slippage_on_the_stop():
    """El fill real casi nunca cae en el precio exacto: hay spread y gaps."""
    market, _ = _market()
    engine = make_engine(market)
    position = await engine.process_decision(_decision())
    assert position is not None
    assert position.stop_loss is not None and position.initial_stop is not None

    risk = abs(position.entry_price - position.initial_stop)
    slipped = position.stop_loss - risk * 0.1  # dentro del 20% de tolerancia

    assert engine._infer_external_exit(position, slipped)[0] is ExitReason.STOP_LOSS


# --------------------------------------------------------------------------
# Sizing por símbolo (override de riesgo/notional para instrumentos caros)
# --------------------------------------------------------------------------


def _gold_decision() -> DecisionGenerated:
    return DecisionGenerated(
        source="test",
        decision_id="d1",
        symbol="XAUUSDM",
        action="open_long",
        accepted=True,
        score=80.0,
        confidence=0.8,
        summary="momentum",
    )


def _gold_market() -> tuple[MarketDataService, MarketStateStore]:
    # Precios y spread representativos del caso real de producción.
    return make_market_with_state(
        candles=make_candles([4370.0, 4375.0, 4372.0, 4378.0, 4376.0] * 6, symbol="XAUUSDM"),
        ticker=make_ticker(symbol="XAUUSDM", bid=4377.9, ask=4378.15),
    )


def _with_gold_spec(engine):
    """Injects a real XAUUSD contract (contract_size=100) into the paper broker.

    ``PaperBroker`` no expone ``instrument_spec`` en los tests unitarios
    (siempre asume contract_size=1, transparente para BTC/ETH/USTEC); aquí se
    parchea puntualmente para reproducir el caso real de oro sin necesitar un
    broker MT5 de verdad.
    """
    from app.execution.models import InstrumentSpec

    spec = InstrumentSpec(symbol="XAUUSDM", contract_size=100.0, volume_min=0.01, volume_step=0.01)
    engine._paper.instrument_spec = lambda symbol: spec if symbol == "XAUUSDM" else None
    return engine


async def test_symbol_matches_the_real_gold_case_gets_rejected_without_override():
    """Reproduce el caso real: cuenta pequeña, oro sin override, se rechaza."""
    market, _ = _gold_market()
    engine = _with_gold_spec(
        make_engine(
            market,
            make_execution_settings(
                initial_balance=440.75,
                sizing={
                    "risk_per_trade_pct": 0.5,
                    "max_position_pct": 20.0,
                    "min_stop_pct": 0.15,
                },
            ),
        )
    )
    position = await engine.process_decision(_gold_decision())
    assert position is None
    assert engine.orders.status()["rejected"] == 1


async def test_symbol_override_lets_gold_open_without_loosening_globals():
    """Con el override SOLO en XAUUSDM (sizing y riesgo), la cuenta puede abrir oro.

    Todos los topes se dejan en valores que por sí solos bloquearían un lote
    de oro (los globales de producción de referencia: 400/400/800%): sólo el
    override específico de XAUUSDM debe abrir la puerta.
    """
    market, _ = _gold_market()
    engine = _with_gold_spec(
        make_engine(
            market,
            make_execution_settings(
                initial_balance=440.75,
                sizing={
                    "risk_per_trade_pct": 0.5,
                    "max_position_pct": 400.0,
                    "min_stop_pct": 0.15,
                    "risk_per_trade_pct_by_symbol": {"XAUUSDM": 2.0},
                    "max_position_pct_by_symbol": {"XAUUSDM": 1100.0},
                },
                risk={
                    "max_exposure_pct": 2000.0,
                    "max_symbol_exposure_pct": 400.0,
                    "max_correlation_exposure_pct": 800.0,
                    "max_symbol_exposure_pct_by_symbol": {"XAUUSDM": 1100.0},
                    "max_correlation_exposure_pct_by_symbol": {"XAUUSDM": 1100.0},
                },
            ),
        )
    )
    position = await engine.process_decision(_gold_decision())
    assert position is not None
    assert position.quantity > 0
    assert position.quantity == pytest.approx(0.01, abs=1e-9)


# --------------------------------------------------------------------------
# Reductor de riesgo por volatilidad alta. El VolatilityFilter solo bloquea
# LOW ("sin rango no hay scalp"); HIGH nunca frenaba nada -- se clasificaba y
# se tiraba. Reutiliza `atr_pct_high_for` para reducir el riesgo por operacion
# de forma gradual en vez de dejarlo fijo, sin bloquear nunca del todo.
# --------------------------------------------------------------------------


def _vol_view(atr_pct: float | None) -> _MarketView:
    return _MarketView(
        atr=None,
        atr_pct=atr_pct,
        spread_bps=None,
        regime="ranging",
        volatility="normal",
        volume=None,
        last_price=None,
        session="america",
    )


def test_volatility_multiplier_is_full_at_or_below_the_threshold():
    market, _ = _market()
    engine = make_engine(market, atr_pct_high_for=lambda symbol: 0.10)

    assert engine._volatility_risk_multiplier(_vol_view(0.05), "XAUUSDM") == 1.0
    assert engine._volatility_risk_multiplier(_vol_view(0.10), "XAUUSDM") == 1.0


def test_volatility_multiplier_shrinks_proportionally_above_the_threshold():
    """El doble del umbral -> mitad de riesgo (high/atr_pct)."""
    market, _ = _market()
    engine = make_engine(market, atr_pct_high_for=lambda symbol: 0.10)

    assert engine._volatility_risk_multiplier(_vol_view(0.20), "XAUUSDM") == pytest.approx(0.5)


def test_volatility_multiplier_never_goes_below_the_configured_floor():
    """Nunca a 0: mismo principio que los frenos de perdida por periodo."""
    market, _ = _market()
    settings = make_execution_settings()
    settings.sizing.volatility_risk_floor = 0.25
    engine = make_engine(market, settings, atr_pct_high_for=lambda symbol: 0.10)

    # 100x el umbral daria un multiplicador ~0.001 sin el piso.
    assert engine._volatility_risk_multiplier(_vol_view(10.0), "XAUUSDM") == pytest.approx(0.25)


def test_volatility_multiplier_never_increases_size():
    """Un piso mal puesto por encima de 1.0 no puede subir el tamano."""
    market, _ = _market()
    settings = make_execution_settings()
    settings.sizing.volatility_risk_floor = 5.0  # valor absurdo, defensivo
    engine = make_engine(market, settings, atr_pct_high_for=lambda symbol: 0.10)

    assert engine._volatility_risk_multiplier(_vol_view(0.05), "XAUUSDM") == 1.0


def test_volatility_multiplier_is_neutral_without_atr_data():
    """Ausencia de medicion no es penalizacion (misma regla que el Bloque 11)."""
    market, _ = _market()
    engine = make_engine(market, atr_pct_high_for=lambda symbol: 0.10)

    assert engine._volatility_risk_multiplier(_vol_view(None), "XAUUSDM") == 1.0


def test_volatility_multiplier_is_neutral_without_threshold_wired():
    """Sin `atr_pct_high_for` cableado (Quant Core apagado), no penaliza."""
    market, _ = _market()
    engine = make_engine(market)  # atr_pct_high_for=None por defecto

    assert engine._volatility_risk_multiplier(_vol_view(50.0), "XAUUSDM") == 1.0


def test_volatility_risk_can_be_disabled():
    market, _ = _market()
    settings = make_execution_settings()
    settings.sizing.volatility_risk_enabled = False
    engine = make_engine(market, settings, atr_pct_high_for=lambda symbol: 0.10)

    assert engine._volatility_risk_multiplier(_vol_view(50.0), "XAUUSDM") == 1.0


def test_volatility_multiplier_uses_the_symbol_specific_threshold():
    market, _ = _market()
    thresholds = {"XAUUSDM": 0.07, "ETHUSDM": 0.14}
    engine = make_engine(market, atr_pct_high_for=lambda symbol: thresholds[symbol])

    # Mismo ATR%, distinto umbral por simbolo -> distinto multiplicador.
    xau = engine._volatility_risk_multiplier(_vol_view(0.14), "XAUUSDM")
    eth = engine._volatility_risk_multiplier(_vol_view(0.14), "ETHUSDM")
    assert xau < 1.0
    assert eth == 1.0


async def test_high_volatility_shrinks_the_actual_position():
    """Extremo a extremo: el reductor llega de verdad al tamano de la posicion."""
    market, _ = _market()
    settings = make_execution_settings()
    settings.sizing.volatility_risk_floor = 0.1
    calm = make_engine(market, settings, atr_pct_high_for=lambda symbol: 100.0)
    volatile = make_engine(market, settings, atr_pct_high_for=lambda symbol: 0.01)

    async def calm_view(symbol: str) -> _MarketView:
        return _vol_view(0.02)

    async def volatile_view(symbol: str) -> _MarketView:
        return _vol_view(0.02)

    calm._market_view = calm_view  # type: ignore[method-assign]
    volatile._market_view = volatile_view  # type: ignore[method-assign]

    calm_position = await calm.process_decision(_decision())
    volatile_position = await volatile.process_decision(_decision())

    assert calm_position is not None and volatile_position is not None
    assert volatile_position.quantity < calm_position.quantity

"""Position sizing y Paper Engine."""

import random

import pytest
from app.config.settings import SizingSettings
from app.execution.commission import CommissionEngine
from app.execution.latency import LatencyEngine
from app.execution.models import InstrumentSpec, OrderRequest, OrderSide
from app.execution.paper_engine import PaperBroker
from app.execution.sizing import PositionSizer
from app.execution.slippage import SlippageContext, SlippageEngine

from tests.unit.execution_helpers import make_execution_settings
from tests.unit.quant_helpers import make_ticker


def test_fixed_risk_sizing_respects_risk_amount():
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=1.0, max_position_pct=100.0)
    )
    result = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=2.0)
    # 1% de 10.000 = 100 arriesgados / distancia 2 = 50 unidades.
    assert result.quantity == 50.0
    assert result.risk_amount == 100.0


def test_percent_sizing_uses_equity_notional():
    sizer = PositionSizer(
        SizingSettings(method="percent", percent_of_equity=10.0, max_position_pct=100.0)
    )
    result = sizer.calculate(equity=10_000.0, price=50.0, stop_distance=1.0)
    assert result.notional == 1_000.0  # 10% de 10.000
    assert result.quantity == 20.0


def test_max_position_cap_limits_quantity():
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=50.0, max_position_pct=5.0)
    )
    result = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=1.0)
    # Tope de nocional = 5% de 10.000 = 500 → 5 unidades a 100.
    assert result.quantity == 5.0


def test_kelly_falls_back_without_history():
    sizer = PositionSizer(SizingSettings(method="kelly", risk_per_trade_pct=1.0))
    result = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=2.0)
    assert result.quantity > 0
    assert "riesgo fijo" in result.reason


def test_paper_fill_applies_costs_against_taker():
    cfg = make_execution_settings()
    rng = random.Random(3)
    broker = PaperBroker(
        cfg,
        CommissionEngine(cfg.commission),
        SlippageEngine(cfg.slippage, rng),
        LatencyEngine(cfg.latency, rng),
        rng,
    )
    ticker = make_ticker(bid=100.0, ask=100.05)
    req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, quantity=1.0)
    result = broker.execute(req, ticker, SlippageContext(order_quantity=1.0, session="europe"))
    assert result.accepted
    fill = result.fill
    assert fill is not None
    # Compra: se ejecuta por encima del ask (peor precio) y paga comisión.
    assert fill.price >= ticker.ask
    assert fill.commission > 0
    assert fill.reference_price == ticker.ask


def test_paper_rejects_bad_market_and_simulated():
    cfg = make_execution_settings(reject_probability=1.0)
    rng = random.Random(0)
    broker = PaperBroker(
        cfg,
        CommissionEngine(cfg.commission),
        SlippageEngine(cfg.slippage, rng),
        LatencyEngine(cfg.latency, rng),
        rng,
    )
    ticker = make_ticker(bid=100.0, ask=100.05)
    req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, quantity=1.0)
    assert not broker.execute(req, ticker).accepted
    # allow_reject=False fuerza el fill (los cierres nunca se atascan).
    assert broker.execute(req, ticker, allow_reject=False).accepted


def test_paper_sell_executes_below_bid():
    cfg = make_execution_settings()
    rng = random.Random(9)
    broker = PaperBroker(
        cfg,
        CommissionEngine(cfg.commission),
        SlippageEngine(cfg.slippage, rng),
        LatencyEngine(cfg.latency, rng),
        rng,
    )
    ticker = make_ticker(bid=100.0, ask=100.05)
    req = OrderRequest(symbol="BTCUSDT", side=OrderSide.SELL, quantity=1.0)
    fill = broker.execute(req, ticker).fill
    assert fill is not None and fill.price <= ticker.bid


def test_sizing_returns_lots_not_units_with_contract_size():
    """El bug histórico del oro: XAUUSD tiene contract_size=100, así que
    ``quantity`` (lotes) debe ser 100× menor que las unidades (onzas)."""
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=1.0, max_position_pct=1000.0)
    )
    spec = InstrumentSpec(symbol="XAUUSD", contract_size=100.0, volume_min=0.01, volume_step=0.01)
    result = sizer.calculate(equity=100_000.0, price=4_000.0, stop_distance=4.0, spec=spec)

    # 1% de 100.000 = 1.000 arriesgados / 4 = 250 onzas = 2.5 lotes.
    assert result.units == 250.0
    assert result.quantity == 2.5
    assert result.notional == 250.0 * 4_000.0
    assert result.risk_amount == 1_000.0


def test_sizing_rejects_when_min_lot_exceeds_risk_budget():
    """Con equity pequeño, el lote mínimo del oro arriesga mucho más que el
    presupuesto: hay que rechazar limpio, no inflar hasta ``volume_min``."""
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=0.5, max_position_pct=1000.0)
    )
    spec = InstrumentSpec(symbol="XAUUSD", contract_size=100.0, volume_min=0.01, volume_step=0.01)
    result = sizer.calculate(equity=164.0, price=4_089.0, stop_distance=6.13, spec=spec)

    assert result.quantity == 0.0
    assert "lote mínimo" in result.reason


def test_sizing_without_spec_keeps_paper_behaviour():
    """Sin spec (paper broker) el resultado es idéntico al de siempre."""
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=1.0, max_position_pct=100.0)
    )
    result = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=2.0)

    assert result.quantity == 50.0
    assert result.units == 50.0
    assert result.notional == 5_000.0


def test_sizing_quantizes_lots_downwards():
    """Se redondea hacia abajo al paso: nunca más riesgo del solicitado."""
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=1.0, max_position_pct=1000.0)
    )
    spec = InstrumentSpec(symbol="ETHUSD", contract_size=1.0, volume_min=0.1, volume_step=0.01)
    result = sizer.calculate(equity=10_000.0, price=2_000.0, stop_distance=57.0, spec=spec)

    # 100 / 57 = 1.7543... unidades = lotes -> 1.75 tras cuantizar hacia abajo.
    assert result.quantity == 1.75
    assert result.risk_amount <= 100.0


# --------------------------------------------------------------------------
# Overrides por símbolo (riesgo por operación y tope de notional)
# --------------------------------------------------------------------------
#
# El lote mínimo de XAUUSD (contract_size=100, ~4300 USD/onza) representa
# ~10x más riesgo y notional que el de BTC/ETH/USTEC (contract_size=1) al
# mismo tamaño de cuenta. Un único par de porcentajes globales o deja a oro
# sin poder abrir el lote mínimo, o afloja la protección del resto de
# símbolos si se sube para que oro quepa. Reproduce el caso real medido en
# producción: cuenta de 440.75 con oro a ~4378, sin overrides.


def _gold_case_settings(**overrides) -> SizingSettings:
    base: dict[str, object] = {
        "method": "fixed_risk",
        "risk_per_trade_pct": 0.5,
        "max_position_pct": 20.0,
    }
    base.update(overrides)
    return SizingSettings(**base)


def _gold_spec() -> InstrumentSpec:
    return InstrumentSpec(symbol="XAUUSDM", contract_size=100.0, volume_min=0.01, volume_step=0.01)


def test_global_defaults_cannot_size_one_lot_of_gold_on_a_small_account():
    """El caso real: globales por defecto, oro nunca cabe con esta cuenta."""
    sizer = PositionSizer(_gold_case_settings())
    result = sizer.calculate(
        equity=440.75, price=4_378.0, stop_distance=6.567, spec=_gold_spec(), symbol="XAUUSDM"
    )
    assert result.quantity == 0.0


def test_symbol_override_lets_gold_size_the_minimum_lot():
    """Con overrides SOLO para XAUUSDM, el lote mínimo de oro cabe."""
    settings = _gold_case_settings(
        risk_per_trade_pct_by_symbol={"XAUUSDM": 2.0},
        max_position_pct_by_symbol={"XAUUSDM": 1100.0},
    )
    sizer = PositionSizer(settings)
    result = sizer.calculate(
        equity=440.75, price=4_378.0, stop_distance=6.567, spec=_gold_spec(), symbol="XAUUSDM"
    )
    assert result.quantity >= 0.01
    assert result.quantity == pytest.approx(0.01, abs=1e-9)


def test_symbol_override_does_not_loosen_other_symbols():
    """El override de oro no afecta a un símbolo sin entrada propia."""
    settings = _gold_case_settings(
        risk_per_trade_pct_by_symbol={"XAUUSDM": 2.0},
        max_position_pct_by_symbol={"XAUUSDM": 1100.0},
    )
    sizer = PositionSizer(settings)
    spec = InstrumentSpec(symbol="ETHUSDM", contract_size=1.0, volume_min=0.01, volume_step=0.01)
    with_symbol = sizer.calculate(
        equity=440.75, price=1_861.48, stop_distance=8.17, spec=spec, symbol="ETHUSDM"
    )
    without_override = PositionSizer(_gold_case_settings()).calculate(
        equity=440.75, price=1_861.48, stop_distance=8.17, spec=spec
    )
    assert with_symbol.quantity == without_override.quantity
    assert with_symbol.risk_amount == without_override.risk_amount


def test_no_symbol_argument_falls_back_to_global_pct():
    """Compatibilidad: sin `symbol`, se comporta exactamente como antes."""
    settings = _gold_case_settings(
        risk_per_trade_pct_by_symbol={"XAUUSDM": 5.0}, max_position_pct=100.0
    )
    sizer = PositionSizer(settings)
    without_symbol = sizer.calculate(equity=10_000.0, price=100.0, stop_distance=2.0)
    # 0.5% global, no el 5% de XAUUSDM: no se pasó symbol.
    assert without_symbol.risk_amount == 50.0


def test_settings_resolvers_fall_back_to_global_for_unlisted_symbols():
    settings = SizingSettings(
        risk_per_trade_pct=0.5,
        max_position_pct=20.0,
        risk_per_trade_pct_by_symbol={"XAUUSDM": 2.0},
        max_position_pct_by_symbol={"XAUUSDM": 1100.0},
    )
    assert settings.risk_per_trade_pct_for("XAUUSDM") == 2.0
    assert settings.risk_per_trade_pct_for("ETHUSDM") == 0.5
    assert settings.max_position_pct_for("xauusdm") == 1100.0  # normaliza a mayúsculas
    assert settings.max_position_pct_for("BTCUSDM") == 20.0


# ----------------------------------------------------------------------
# Excepcion del lote minimo (indivisible)
# ----------------------------------------------------------------------

_XAU = InstrumentSpec(symbol="XAUUSD", contract_size=100.0, volume_min=0.01, volume_step=0.01)


def test_min_lot_exception_is_off_by_default():
    """El comportamiento historico se conserva: sin configurar, rechaza."""
    sizer = PositionSizer(
        SizingSettings(method="fixed_risk", risk_per_trade_pct=0.5, max_position_pct=1000.0)
    )
    result = sizer.calculate(equity=400.0, price=4_495.0, stop_distance=6.74, spec=_XAU)

    assert result.quantity == 0.0
    assert "no cabe en el riesgo" in result.reason


def test_min_lot_exception_lets_the_engine_keep_trading_on_a_small_account():
    """El caso del 2026-08-20: con 400 USD el presupuesto (2.00) no da para el
    lote minimo (6.74 de riesgo), y el motor dejaba de operar en silencio."""
    sizer = PositionSizer(
        SizingSettings(
            method="fixed_risk",
            risk_per_trade_pct=0.5,
            max_position_pct=1000.0,
            min_lot_max_risk_pct=2.0,  # 2 % de 400 = 8.00 > 6.74
        )
    )
    result = sizer.calculate(equity=400.0, price=4_495.0, stop_distance=6.74, spec=_XAU)

    assert result.quantity == 0.01
    assert result.units == 1.0
    assert result.risk_amount == pytest.approx(6.74)
    assert "tope" in result.reason


def test_min_lot_exception_still_refuses_above_the_ceiling():
    """El tope es la proteccion real: por encima, se rechaza igual que antes."""
    sizer = PositionSizer(
        SizingSettings(
            method="fixed_risk",
            risk_per_trade_pct=0.5,
            max_position_pct=1000.0,
            min_lot_max_risk_pct=1.0,  # 1 % de 400 = 4.00 < 6.74
        )
    )
    result = sizer.calculate(equity=400.0, price=4_495.0, stop_distance=6.74, spec=_XAU)

    assert result.quantity == 0.0
    assert "no cabe en el riesgo" in result.reason


def test_min_lot_exception_scales_with_equity_without_touching_config():
    """Lo que pedia el operador: mismo ajuste sirviendo para 200, 300 o 400 USD.

    Con el tope al 3.5 %, el lote minimo de oro (6.74 de riesgo) cabe desde
    ~193 USD de cuenta hacia arriba, sin tocar nada entre medias.
    """
    sizer = PositionSizer(
        SizingSettings(
            method="fixed_risk",
            risk_per_trade_pct=0.5,
            max_position_pct=1000.0,
            min_lot_max_risk_pct=3.5,
        )
    )
    for equity in (200.0, 300.0, 400.0):
        result = sizer.calculate(equity=equity, price=4_495.0, stop_distance=6.74, spec=_XAU)
        assert result.quantity == 0.01, equity

    # Y por debajo deja de operar, que es lo correcto: 3.5 % de 150 son 5.25.
    assert (
        sizer.calculate(equity=150.0, price=4_495.0, stop_distance=6.74, spec=_XAU).quantity == 0.0
    )


def test_min_lot_exception_never_inflates_a_lot_that_already_fits():
    """No es una puerta trasera: si el presupuesto da para mas, manda el sizing."""
    sizer = PositionSizer(
        SizingSettings(
            method="fixed_risk",
            risk_per_trade_pct=1.0,
            max_position_pct=1000.0,
            min_lot_max_risk_pct=5.0,
        )
    )
    result = sizer.calculate(equity=100_000.0, price=4_000.0, stop_distance=4.0, spec=_XAU)

    assert result.quantity == 2.5  # el mismo de siempre, no volume_min

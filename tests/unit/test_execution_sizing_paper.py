"""Position sizing y Paper Engine."""

import random

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
    spec = InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, volume_min=0.01, volume_step=0.01
    )
    result = sizer.calculate(
        equity=100_000.0, price=4_000.0, stop_distance=4.0, spec=spec
    )

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
    spec = InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, volume_min=0.01, volume_step=0.01
    )
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

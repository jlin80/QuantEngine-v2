"""Motores de simulación: comisiones, slippage y latencia."""

import random

from app.config.settings import CommissionSettings, LatencySettings, SlippageSettings
from app.execution.commission import CommissionEngine
from app.execution.latency import LatencyEngine
from app.execution.models import OrderType
from app.execution.slippage import SlippageContext, SlippageEngine


def test_commission_per_notional_and_minimum():
    engine = CommissionEngine(CommissionSettings(model="per_notional", taker_bps=2.0, minimum=0.5))
    quote = engine.calculate("BTCUSDT", quantity=1.0, price=10_000.0)
    assert quote.amount == 2.0  # 2 bps de 10.000
    assert quote.liquidity == "taker"
    # El mínimo se respeta en operaciones diminutas.
    small = engine.calculate("BTCUSDT", quantity=0.0001, price=100.0)
    assert small.amount == 0.5


def test_commission_per_symbol_override():
    engine = CommissionEngine(
        CommissionSettings(
            model="per_notional", taker_bps=2.0, per_symbol={"XAUUSD": {"taker_bps": 5.0}}
        )
    )
    assert engine.calculate("XAUUSD", 1.0, 2000.0).amount == 1.0  # 5 bps
    assert engine.calculate("BTCUSDT", 1.0, 2000.0).amount == 0.4  # 2 bps


def test_slippage_grows_with_volatility_and_never_negative():
    engine = SlippageEngine(
        SlippageSettings(model="dynamic", base_bps=1.0, volatility_coeff=1.0), random.Random(0)
    )
    calm = engine.estimate_bps(SlippageContext(atr_pct=0.1, order_quantity=1.0, session="europe"))
    wild = engine.estimate_bps(SlippageContext(atr_pct=5.0, order_quantity=1.0, session="europe"))
    assert wild > calm >= 0.0


def test_slippage_none_model_is_zero():
    engine = SlippageEngine(SlippageSettings(model="none"))
    assert engine.estimate_bps(SlippageContext(atr_pct=9.0)) == 0.0


def test_slippage_stop_orders_pay_more():
    settings = SlippageSettings(model="dynamic", base_bps=1.0, stop_order_extra_bps=5.0)
    engine = SlippageEngine(settings, random.Random(0))
    market = engine.estimate_bps(SlippageContext(order_type=OrderType.MARKET, session="europe"))
    stop = engine.estimate_bps(SlippageContext(order_type=OrderType.STOP_MARKET, session="europe"))
    assert stop > market


def test_latency_disabled_is_zero():
    engine = LatencyEngine(LatencySettings(enabled=False))
    quote = engine.sample()
    assert quote.total_ms == 0.0 and quote.drift_bps == 0.0


def test_latency_accumulates_and_drifts_price():
    engine = LatencyEngine(
        # `enabled` explicito: el default es False (la latencia real ya esta
        # dentro de lo observado en los stops); aqui se prueba el motor, no el default.
        LatencySettings(
            enabled=True, network_ms=20, broker_ms=15, exchange_ms=10, internal_ms=5, jitter_ms=0
        ),
        random.Random(1),
    )
    quote = engine.sample()
    assert quote.total_ms == 50.0
    assert quote.drift_bps > 0.0

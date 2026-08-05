"""Execution Optimizer (Bloque 6): coste esperado, llenado y elección."""

import random

import pytest
from app.config.settings import ExecutionOptimizerSettings, LatencySettings, SlippageSettings
from app.execution.latency.engine import LatencyEngine
from app.execution.models.enums import OrderType
from app.execution.optimizer import TACTICS, ExecutionContext, ExecutionOptimizer
from app.execution.slippage.engine import SlippageEngine


def _optimizer(*, slippage_model: str = "dynamic", **overrides) -> ExecutionOptimizer:
    """Optimizador determinista: sin jitter, para poder comparar cifras."""
    settings = ExecutionOptimizerSettings(**overrides)
    slippage = SlippageEngine(SlippageSettings(model=slippage_model), rng=random.Random(0))
    latency = LatencyEngine(LatencySettings(jitter_ms=0.0), rng=random.Random(0))
    return ExecutionOptimizer(settings, slippage, latency)


def _context(**overrides) -> ExecutionContext:
    defaults = {"symbol": "BTCUSDm", "quantity": 0.01, "spread_bps": 4.0, "atr_pct": 0.05}
    return ExecutionContext(**{**defaults, **overrides})


def test_every_tactic_is_quoted_before_choosing() -> None:
    plan = _optimizer().optimize(_context())
    quoted = {plan.chosen.tactic} | {q.tactic for q in plan.alternatives}
    assert quoted == set(TACTICS)


def test_the_discarded_options_travel_with_the_decision() -> None:
    # Sin ellas, "se eligió MARKET" no se puede auditar: no se sabe por cuánto
    # ganó ni frente a qué.
    plan = _optimizer().optimize(_context())
    assert len(plan.alternatives) == 2
    assert plan.reason
    assert "bps" in plan.reason


def test_market_always_fills_and_a_limit_does_not() -> None:
    optimizer = _optimizer()
    assert optimizer.quote("market", _context()).fill_probability == 1.0
    assert optimizer.quote("limit", _context()).fill_probability < 1.0


def test_a_limit_earns_the_spread_and_market_pays_it() -> None:
    optimizer = _optimizer()
    assert optimizer.quote("market", _context(spread_bps=10.0)).spread_cost_bps == pytest.approx(
        5.0
    )
    assert optimizer.quote("limit", _context(spread_bps=10.0)).spread_cost_bps == pytest.approx(
        -5.0
    )


def test_a_passive_limit_never_suffers_slippage_but_an_ioc_does() -> None:
    # El límite pasivo se llena a su precio o no se llena: su riesgo es el otro.
    # El IOC es agresivo, cruza el spread, y por tanto sí sufre slippage.
    optimizer = _optimizer()
    assert optimizer.quote("limit", _context()).expected_slippage_bps == 0.0
    assert optimizer.quote("ioc", _context()).expected_slippage_bps > 0.0


def test_an_ioc_pays_the_spread_like_a_market_order() -> None:
    # Modelarlo como si lo cobrara le daba la ventaja del pasivo Y la del
    # agresivo a la vez, y con eso ganaba siempre.
    optimizer = _optimizer()
    assert optimizer.quote("ioc", _context(spread_bps=10.0)).spread_cost_bps == pytest.approx(5.0)


def test_urgency_is_what_makes_market_win() -> None:
    # Sin coste de no ejecutar, LIMIT ganaría siempre y el optimizador sería una
    # máquina de no operar.
    optimizer = _optimizer()
    calm = optimizer.optimize(_context(urgency=0.0))
    urgent = optimizer.optimize(_context(urgency=1.0))
    assert calm.chosen.tactic != "market"
    assert urgent.chosen.tactic == "market"


def test_a_favourable_book_makes_a_limit_more_likely_to_fill() -> None:
    optimizer = _optimizer()
    against = optimizer.quote("limit", _context(queue_imbalance=-1.0)).fill_probability
    neutral = optimizer.quote("limit", _context(queue_imbalance=0.0)).fill_probability
    favourable = optimizer.quote("limit", _context(queue_imbalance=1.0)).fill_probability
    assert against < neutral < favourable


def test_without_an_observable_book_the_fill_estimate_is_not_invented() -> None:
    # Es justo el escenario (MT5, sin libro) en el que un optimizador demasiado
    # confiado empieza a preferir límites que nunca se llenan.
    optimizer = _optimizer(limit_fill_base=0.55)
    assert optimizer.quote("limit", _context(queue_imbalance=None)).fill_probability == 0.55


def test_fill_probability_stays_within_bounds() -> None:
    optimizer = _optimizer(limit_fill_base=0.9, imbalance_coeff=5.0)
    assert optimizer.quote("limit", _context(queue_imbalance=1.0)).fill_probability == 1.0
    assert optimizer.quote("limit", _context(queue_imbalance=-1.0)).fill_probability == 0.0


def test_an_ioc_sits_between_a_limit_and_a_market_order() -> None:
    optimizer = _optimizer()
    limit = optimizer.quote("limit", _context()).fill_probability
    ioc = optimizer.quote("ioc", _context()).fill_probability
    assert limit < ioc < 1.0


def test_a_wider_spread_makes_market_more_expensive() -> None:
    optimizer = _optimizer()
    tight = optimizer.quote("market", _context(spread_bps=1.0)).expected_cost_bps
    wide = optimizer.quote("market", _context(spread_bps=40.0)).expected_cost_bps
    assert wide > tight


def test_the_quality_score_only_reorders_what_the_cost_already_says() -> None:
    optimizer = _optimizer()
    cheap = optimizer.quote("market", _context(spread_bps=1.0))
    dear = optimizer.quote("market", _context(spread_bps=40.0))
    assert cheap.quality_score > dear.quality_score
    assert 0.0 <= dear.quality_score <= 100.0


def test_ties_are_won_by_the_tactic_that_actually_executes() -> None:
    # Ante coste esperado igual, la táctica que sí ejecuta es la que cumple la
    # decisión que el motor ya tomó.
    optimizer = _optimizer(
        slippage_model="none", miss_cost_bps=0.0, limit_fill_base=1.0, ioc_fill_base=1.0
    )
    plan = optimizer.optimize(_context(spread_bps=0.0, atr_pct=0.0, quantity=0.0))
    assert plan.chosen.tactic == "market"


def test_tactics_map_to_real_order_types() -> None:
    optimizer = _optimizer()
    assert optimizer.quote("market", _context()).order_type is OrderType.MARKET
    assert optimizer.quote("limit", _context()).order_type is OrderType.LIMIT
    assert optimizer.quote("ioc", _context()).order_type is OrderType.LIMIT

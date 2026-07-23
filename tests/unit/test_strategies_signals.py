"""Biblioteca de estrategias: escenarios dirigidos, barrido, determinismo y
rendimiento."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.engine.feature_store import FeatureStore
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction
from app.engine.validators import SignalValidator
from app.market.models import TradeSide
from app.market.services import MarketDataService
from app.strategies.base import QuantStrategy
from app.strategies.breakout.opening_range_breakout import OpeningRangeBreakout
from app.strategies.breakout.range_breakout import RangeBreakout
from app.strategies.breakout.vwap_breakout import VWAPBreakout
from app.strategies.mean_reversion.mean_reversion import MeanReversion
from app.strategies.mean_reversion.vwap_mean_reversion import VWAPMeanReversion
from app.strategies.momentum.momentum_continuation import MomentumContinuation
from app.strategies.orderflow.cvd import CumulativeVolumeDelta
from app.strategies.orderflow.delta_confirmation import DeltaConfirmation
from app.strategies.orderflow.orderbook_imbalance import OrderBookImbalance
from app.strategies.smc.bos import BreakOfStructure
from app.strategies.smc.choch import ChangeOfCharacter
from app.strategies.smc.fair_value_gap import FairValueGapStrategy
from app.strategies.smc.liquidity_sweep import LiquiditySweep
from app.strategies.smc.mss import MarketStructureShift
from app.strategies.smc.order_block import OrderBlock
from app.strategies.trend.anchored_vwap import AnchoredVWAP
from app.strategies.trend.trend_pullback import TrendPullback
from app.strategies.volatility.atr_expansion import ATRExpansion
from app.strategies.volatility.volatility_compression import VolatilityCompression
from app.strategies.volume.volume_profile import VolumeProfileStrategy
from app.utils.time import utc_now

from tests.unit.quant_helpers import (
    make_book,
    make_candles,
    make_context,
    make_market,
    make_trade,
)

ALL_STRATEGIES: list[type[QuantStrategy]] = [
    VWAPMeanReversion,
    VWAPBreakout,
    AnchoredVWAP,
    LiquiditySweep,
    OrderBlock,
    FairValueGapStrategy,
    BreakOfStructure,
    ChangeOfCharacter,
    MarketStructureShift,
    DeltaConfirmation,
    CumulativeVolumeDelta,
    OrderBookImbalance,
    VolumeProfileStrategy,
    OpeningRangeBreakout,
    MomentumContinuation,
    ATRExpansion,
    VolatilityCompression,
    TrendPullback,
    MeanReversion,
    RangeBreakout,
]

FIRED = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)


def _ctx(
    market: MarketDataService,
    *,
    fired_at: datetime | None = None,
    **context_overrides: Any,
) -> AnalysisContext:
    return AnalysisContext(
        symbol="BTCUSDT",
        fired_at=fired_at or utc_now(),
        trigger="test",
        market=market,
        features=FeatureStore(market),
        context=make_context(data_quality=1.0, last_price=100.0, **context_overrides),
    )


def _rich_market() -> MarketDataService:
    """Mercado sintético variado: velas, trades bilaterales, libro y ticker."""
    closes = [100.0 + (i % 11) * 0.4 - (i % 5) * 0.2 for i in range(120)]
    trades = [
        make_trade(
            price=closes[-1],
            size=1.0 + (i % 3),
            side=TradeSide.BUY if i % 3 else TradeSide.SELL,
        )
        for i in range(60)
    ]
    return make_market(
        candles=make_candles(closes),
        trades=trades,
        book=make_book(bids=[(99.9, 5.0), (99.8, 4.0)], asks=[(100.1, 5.0), (100.2, 4.0)]),
    )


# ---------------------------------------------------------------------------
# Barrido: las 20 estrategias corren sin errores y emiten señales válidas.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_cls", ALL_STRATEGIES, ids=lambda c: c.name)
async def test_every_strategy_runs_and_emits_valid_or_nothing(strategy_cls):
    strategy = strategy_cls()
    signal = await strategy.analyze(_ctx(_rich_market()))
    if signal is not None:
        assert SignalValidator().validate(signal, now=utc_now()) == []
        assert signal.reasons, "explicabilidad obligatoria"
        assert signal.entry_zone is not None
        assert signal.stop_loss is not None
        assert signal.take_profit is not None


def test_all_twenty_strategies_have_unique_names_and_categories():
    names = [cls.name for cls in ALL_STRATEGIES]
    assert len(names) == 20
    assert len(set(names)) == 20
    assert all(cls.category for cls in ALL_STRATEGIES)
    assert all(cls.default_parameters for cls in ALL_STRATEGIES)


# ---------------------------------------------------------------------------
# Escenarios dirigidos (cada uno construye las condiciones exactas).
# ---------------------------------------------------------------------------


async def test_range_breakout_long_scenario():
    closes = [100.0] * 45 + [102.0]
    opens = [100.0] * 45 + [100.1]
    volumes = [10.0] * 45 + [30.0]
    market = make_market(candles=make_candles(closes, opens=opens, volumes=volumes))
    signal = await RangeBreakout().analyze(_ctx(market))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert signal.stop_loss is not None and signal.stop_loss < 102.0
    assert signal.take_profit is not None and signal.take_profit > 102.0
    assert any("Ruptura up" in r for r in signal.reasons)


async def test_volatility_compression_release_scenario():
    closes = [100.0] * 45 + [100.0] * 14 + [100.9]
    highs = [100.4] * 45 + [100.05] * 14 + [100.95]
    lows = [99.6] * 45 + [99.95] * 14 + [100.0]
    opens = [100.0] * 59 + [100.05]
    market = make_market(candles=make_candles(closes, opens=opens, highs=highs, lows=lows))
    signal = await VolatilityCompression().analyze(_ctx(market))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert any("Squeeze" in r for r in signal.reasons)
    assert signal.take_profit is not None and signal.take_profit > 100.9


async def test_mean_reversion_short_scenario():
    closes = [100.0 + (i % 2) * 0.1 for i in range(50)] + [101.8]
    market = make_market(candles=make_candles(closes))
    signal = await MeanReversion().analyze(_ctx(market))
    assert signal is not None
    assert signal.direction is Direction.SHORT
    assert signal.take_profit is not None and signal.take_profit < 101.8
    assert any("σ" in r for r in signal.reasons)


async def test_atr_expansion_long_scenario():
    closes = [100.0] * 40 + [100.5, 101.2, 102.0, 103.0, 104.2]
    opens = [100.0] * 40 + [100.0, 100.5, 101.2, 102.0, 103.0]
    highs = [100.1] * 40 + [100.6, 101.3, 102.1, 103.1, 104.3]
    lows = [99.9] * 40 + [99.9, 100.4, 101.1, 101.9, 102.9]
    market = make_market(candles=make_candles(closes, opens=opens, highs=highs, lows=lows))
    signal = await ATRExpansion().analyze(_ctx(market))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert any("Expansión de volatilidad" in r for r in signal.reasons)


async def test_cvd_accumulation_scenario():
    neutral = [
        make_trade(price=100.0, size=1.0, side=TradeSide.BUY if i % 2 else TradeSide.SELL)
        for i in range(30)
    ]
    buyers = [make_trade(price=100.0, size=2.0, side=TradeSide.BUY) for _ in range(30)]
    market = make_market(candles=make_candles([100.0] * 60), trades=neutral + buyers)
    signal = await CumulativeVolumeDelta().analyze(_ctx(market))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert any("acumulación" in r for r in signal.reasons)


async def test_opening_range_breakout_scenario():
    day_start = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
    opening = [100.0 + (i % 2) * 0.3 for i in range(30)]  # rango ~[99.8, 100.5]
    inside = [100.1] * 30
    closes = opening + inside + [101.2]
    opens = opening + inside + [100.2]
    volumes = [10.0] * 60 + [30.0]
    market = make_market(
        candles=make_candles(closes, opens=opens, volumes=volumes, start=day_start)
    )
    fired = day_start + timedelta(minutes=62)
    signal = await OpeningRangeBreakout().analyze(_ctx(market, fired_at=fired))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert any("opening range" in r for r in signal.reasons)
    # Solo la PRIMERA ruptura: si ya había cierre fuera, no hay señal.
    closes_second = opening + inside[:-1] + [101.0, 101.2]
    market_second = make_market(
        candles=make_candles(closes_second, opens=opens, volumes=volumes, start=day_start)
    )
    repeat = await OpeningRangeBreakout().analyze(_ctx(market_second, fired_at=fired))
    assert repeat is None


async def test_orderbook_imbalance_long_scenario():
    market = make_market(
        candles=make_candles([100.0] * 50),
        book=make_book(
            bids=[(99.9, 10.0), (99.8, 9.0), (99.7, 8.0)],
            asks=[(100.1, 2.0), (100.2, 1.5), (100.3, 1.0)],
        ),
    )
    signal = await OrderBookImbalance().analyze(_ctx(market))
    assert signal is not None
    assert signal.direction is Direction.LONG
    assert any("Imbalance" in r for r in signal.reasons)


# ---------------------------------------------------------------------------
# Determinismo y rendimiento
# ---------------------------------------------------------------------------


async def test_strategy_determinism_same_market_same_signal():
    closes = [100.0] * 45 + [102.0]
    opens = [100.0] * 45 + [100.1]
    volumes = [10.0] * 45 + [30.0]
    market = make_market(candles=make_candles(closes, opens=opens, volumes=volumes))
    features = FeatureStore(market)
    context = make_context(data_quality=1.0, last_price=102.0)

    def ctx() -> AnalysisContext:
        return AnalysisContext(
            symbol="BTCUSDT",
            fired_at=FIRED,
            trigger="test",
            market=market,
            features=features,
            context=context,
        )

    first = await RangeBreakout().analyze(ctx())
    second = await RangeBreakout().analyze(ctx())
    assert first is not None and second is not None
    assert first.score == second.score
    assert first.confidence == second.confidence
    assert first.stop_loss == second.stop_loss
    assert first.take_profit == second.take_profit
    assert first.reasons == second.reasons


async def test_library_sweep_performance():
    """Las 20 estrategias sobre 120 velas deben evaluar en tiempo acotado."""
    market = _rich_market()
    features = FeatureStore(market)  # compartido: el cache evita recomputar
    context = make_context(data_quality=1.0, last_price=100.0)
    started = time.perf_counter()
    for strategy_cls in ALL_STRATEGIES:
        ctx = AnalysisContext(
            symbol="BTCUSDT",
            fired_at=FIRED,
            trigger="perf",
            market=market,
            features=features,
            context=context,
        )
        await strategy_cls().analyze(ctx)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"barrido completo en {elapsed:.2f}s (límite holgado para CI)"
    assert features.stats["hits"] > 0, "el Feature Store debe estar reutilizando cálculos"

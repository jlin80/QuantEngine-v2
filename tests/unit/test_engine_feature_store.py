"""Feature Store: cálculo único, TTL, invalidación y features integradas."""

import asyncio
from typing import Any

import pytest
from app.engine.feature_store import FeatureStore
from app.market.models import TradeSide

from tests.unit.quant_helpers import (
    make_book,
    make_candles,
    make_market,
    make_ticker,
    make_trade,
)


async def test_compute_once_within_ttl():
    store = FeatureStore(make_market(), default_ttl_seconds=1000.0)
    calls = {"n": 0}

    async def counter(symbol: str, params: dict[str, Any]) -> float | None:
        calls["n"] += 1
        return 42.0

    store.register("counter", counter)

    assert await store.get("counter", "BTCUSDT") == 42.0
    assert await store.get("counter", "BTCUSDT") == 42.0
    assert calls["n"] == 1, "la segunda lectura sale del cache"
    assert store.stats["hits"] == 1

    # Parámetros distintos = clave distinta = nuevo cálculo.
    await store.get("counter", "BTCUSDT", period=14)
    assert calls["n"] == 2


async def test_ttl_expiry_recomputes():
    store = FeatureStore(make_market())
    calls = {"n": 0}

    async def counter(symbol: str, params: dict[str, Any]) -> float | None:
        calls["n"] += 1
        return 1.0

    store.register("fast", counter, ttl_seconds=0.01)
    await store.get("fast", "BTCUSDT")
    await asyncio.sleep(0.03)
    await store.get("fast", "BTCUSDT")
    assert calls["n"] == 2


async def test_invalidate_by_symbol():
    store = FeatureStore(make_market())
    calls = {"n": 0}

    async def counter(symbol: str, params: dict[str, Any]) -> float | None:
        calls["n"] += 1
        return 1.0

    store.register("c", counter, ttl_seconds=1000.0)
    await store.get("c", "BTCUSDT")
    await store.get("c", "ETHUSDT")
    store.invalidate("BTCUSDT")
    await store.get("c", "BTCUSDT")  # recalcula
    await store.get("c", "ETHUSDT")  # sigue cacheada
    assert calls["n"] == 3


async def test_unknown_feature_raises():
    store = FeatureStore(make_market())
    with pytest.raises(KeyError):
        await store.get("does_not_exist", "BTCUSDT")


async def test_atr_with_constant_ranges():
    market = make_market(candles=make_candles([100.0] * 20, range_pad=0.5))
    store = FeatureStore(market)
    atr = await store.get("atr", "BTCUSDT", period=14, timeframe="1m")
    assert atr == pytest.approx(1.0)


async def test_ema_stays_within_price_range():
    closes = [100.0 + i * 0.5 for i in range(60)]
    store = FeatureStore(make_market(candles=make_candles(closes)))
    ema = await store.get("ema", "BTCUSDT", period=20, timeframe="1m")
    assert ema is not None
    assert min(closes) <= ema <= max(closes)
    assert ema > 110.0, "la EMA debe estar cargada hacia los precios recientes"


async def test_vwap_weighted_by_volume():
    candles = make_candles([100.0, 110.0], volumes=[10.0, 30.0], vwaps=[100.0, 110.0])
    store = FeatureStore(make_market(candles=candles))
    vwap = await store.get("vwap", "BTCUSDT", lookback=10, timeframe="1m")
    assert vwap == pytest.approx((100.0 * 10 + 110.0 * 30) / 40.0)


async def test_delta_from_trade_sides():
    trades = [
        make_trade(size=2.0, side=TradeSide.BUY),
        make_trade(size=0.5, side=TradeSide.SELL),
        make_trade(size=1.0, side=TradeSide.UNKNOWN),  # no vota
    ]
    store = FeatureStore(make_market(trades=trades))
    delta = await store.get("delta", "BTCUSDT")
    assert delta == pytest.approx(1.5)


async def test_book_and_ticker_features():
    market = make_market(
        ticker=make_ticker(bid=100.0, ask=100.1),
        book=make_book(bids=[(100.0, 8.0)], asks=[(100.1, 2.0)]),
    )
    store = FeatureStore(market)
    spread_bps = await store.get("spread_bps", "BTCUSDT")
    assert spread_bps == pytest.approx(9.995, abs=0.01)
    imbalance = await store.get("imbalance", "BTCUSDT")
    assert imbalance == pytest.approx((8.0 - 2.0) / 10.0)


async def test_missing_data_returns_none():
    store = FeatureStore(make_market())
    assert await store.get("atr", "GHOSTUSD") is None
    assert await store.get("vwap", "GHOSTUSD") is None
    assert await store.get("delta", "GHOSTUSD") is None

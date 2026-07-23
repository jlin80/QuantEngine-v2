"""MarketDataService: la API interna agnóstica del exchange."""

import asyncio
from datetime import UTC, datetime

from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.market.cache import MarketCache
from app.market.models import (
    DepthLevel,
    FundingRate,
    OpenInterest,
    OrderBook,
    Ticker,
    Trade,
    TradeSide,
)
from app.market.services import MarketDataService, MarketStateStore

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _service() -> tuple[MarketDataService, MarketStateStore, MarketCache]:
    state = MarketStateStore()
    cache = MarketCache(CacheService(primary=None, fallback=InMemoryCache()))
    return MarketDataService(state, cache), state, cache


def _trade(price: float = 100.0) -> Trade:
    return Trade(
        symbol="BTCUSDT",
        provider="binance",
        price=price,
        size=1.0,
        side=TradeSide.BUY,
        exchange_ts=_TS,
        local_ts=_TS,
    )


def _ticker(bid: float = 99.0, ask: float = 101.0) -> Ticker:
    return Ticker(
        symbol="BTCUSDT",
        provider="binance",
        bid=bid,
        ask=ask,
        exchange_ts=_TS,
        local_ts=_TS,
    )


async def test_last_price_prefers_trade_then_mid_then_cache():
    service, state, cache = _service()
    assert await service.get_last_price("BTCUSDT") is None

    await cache.set_last_price("BTCUSDT", 42.0, "restart")
    assert await service.get_last_price("BTCUSDT") == 42.0  # solo cache

    state.update_ticker(_ticker())
    assert await service.get_last_price("BTCUSDT") == 100.0  # mid

    state.update_trade(_trade(123.0))
    assert await service.get_last_price("BTCUSDT") == 123.0  # trade manda


def test_spread_from_ticker_or_book():
    service, state, _ = _service()
    assert service.get_spread("BTCUSDT") is None
    book = OrderBook(
        symbol="BTCUSDT",
        provider="binance",
        bids=(DepthLevel(price=99.5, size=1.0),),
        asks=(DepthLevel(price=100.5, size=1.0),),
        exchange_ts=_TS,
        local_ts=_TS,
    )
    state.update_book(book)
    assert service.get_spread("BTCUSDT") == 1.0
    state.update_ticker(_ticker(99.0, 101.0))
    assert service.get_spread("BTCUSDT") == 2.0  # el ticker tiene prioridad


def test_depth_and_orderbook():
    service, state, _ = _service()
    assert service.get_depth("BTCUSDT") is None
    book = OrderBook(
        symbol="BTCUSDT",
        provider="binance",
        bids=(DepthLevel(price=99.5, size=3.0), DepthLevel(price=99.0, size=2.0)),
        asks=(DepthLevel(price=100.5, size=4.0),),
        exchange_ts=_TS,
        local_ts=_TS,
    )
    state.update_book(book)
    assert service.get_orderbook("btcusdt") is book  # case-insensitive
    assert service.get_depth("BTCUSDT", levels=1) == (3.0, 4.0)


def test_snapshot_composes_everything():
    service, state, _ = _service()
    state.update_ticker(_ticker())
    state.update_trade(_trade())
    state.update_funding(
        FundingRate(
            symbol="BTCUSDT", provider="binance", rate=0.0001, exchange_ts=_TS, local_ts=_TS
        )
    )
    state.update_open_interest(
        OpenInterest(
            symbol="BTCUSDT", provider="binance", contracts=1000.0, exchange_ts=_TS, local_ts=_TS
        )
    )
    snapshot = service.get_market_snapshot("BTCUSDT")
    assert snapshot.ticker is not None
    assert snapshot.last_trade is not None
    assert snapshot.funding is not None
    assert snapshot.open_interest is not None
    payload = snapshot.to_dict()
    assert payload["symbol"] == "BTCUSDT"
    assert payload["funding"]["rate"] == 0.0001


def test_market_state_without_feed():
    service, state, _ = _service()
    state.update_trade(_trade())
    market_state = service.get_market_state("BTCUSDT")
    assert market_state.subscribed is False
    assert market_state.connected is False
    assert market_state.ticks_received == 1
    assert service.symbols == ["BTCUSDT"]


async def test_tick_stream_yields_trades():
    service, state, _ = _service()

    async def consume() -> Trade:
        stream = service.get_tick_stream("BTCUSDT")
        return await asyncio.wait_for(anext(stream), timeout=2.0)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # dejar que el listener se registre
    state.update_trade(_trade())
    trade = await task
    assert trade.price == 100.0

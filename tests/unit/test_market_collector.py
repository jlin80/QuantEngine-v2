"""Pipeline del collector: validación → estado → velas → libro → eventos."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import pytest
from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.events.events import PriceUpdated
from app.market.aggregator import CandleAggregator
from app.market.cache import MarketCache
from app.market.collector import TickCollector
from app.market.events import (
    CandleClosed,
    DataQualityAlert,
    NewTick,
    OrderBookResyncRequired,
    OrderBookUpdated,
    TradeReceived,
)
from app.market.models import (
    DepthLevel,
    OrderBookDelta,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.services.orderbook_manager import OrderBookManager
from app.market.services.state import MarketStateStore
from app.market.validator import DataValidator
from app.utils.time import utc_now

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
_E = TypeVar("_E", bound=Event)


class _Harness:
    """Collector real con bus real y cache en memoria."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.state = MarketStateStore()
        self.events: list[Event] = []
        self.collector = TickCollector(
            bus=self.bus,
            validator=DataValidator(stale_seconds=10**9),  # los ts fijos no son stale
            aggregator=CandleAggregator([Timeframe.M1]),
            books=OrderBookManager(),
            state=self.state,
            cache=MarketCache(CacheService(primary=None, fallback=InMemoryCache())),
            writer=None,
        )

    async def __aenter__(self) -> "_Harness":
        await self.bus.start()

        async def sniffer(event: Event) -> None:
            self.events.append(event)

        self.bus.subscribe(sniffer)
        await self.collector.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.collector.stop()
        await self.bus.stop()

    async def drain(self) -> None:
        """Wait until collector queue and bus queue are processed."""

        async def _poll() -> None:
            while self.collector.queue_depth > 0 or self.bus.stats.queue_size > 0:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)  # margen para el dispatch final

        await asyncio.wait_for(_poll(), 2.0)

    def events_of(self, event_type: type[_E]) -> list[_E]:
        return [event for event in self.events if isinstance(event, event_type)]


def _trade(price: float = 100.0, offset_s: float = 0.0, trade_id: str = "") -> Trade:
    ts = _TS + timedelta(seconds=offset_s)
    return Trade(
        symbol="BTCUSDT",
        provider="binance",
        trade_id=trade_id,
        price=price,
        size=1.0,
        side=TradeSide.BUY,
        exchange_ts=ts,
        local_ts=ts,
    )


@pytest.fixture()
async def harness():
    async with _Harness() as h:
        yield h


async def test_trade_flows_to_state_events_and_metrics(harness: _Harness):
    harness.collector.submit(_trade())
    await harness.drain()

    assert harness.state.last_trade("BTCUSDT") is not None
    assert harness.state.tick_count("BTCUSDT") == 1
    assert len(harness.events_of(NewTick)) == 1
    assert len(harness.events_of(TradeReceived)) == 1
    assert harness.collector.metrics.ticks.total == 1


async def test_invalid_trade_is_rejected_with_alert(harness: _Harness):
    harness.collector.submit(_trade(price=-1.0))
    await harness.drain()

    assert harness.state.last_trade("BTCUSDT") is None
    alerts = harness.events_of(DataQualityAlert)
    assert len(alerts) == 1
    assert alerts[0].issue == "negative_price"
    assert harness.collector.metrics.rejected == 1


async def test_candle_closes_when_bucket_rolls(harness: _Harness):
    harness.collector.submit(_trade(price=100.0, offset_s=0))
    harness.collector.submit(_trade(price=105.0, offset_s=30))
    harness.collector.submit(_trade(price=110.0, offset_s=70))  # cierra la 1m
    await harness.drain()

    closed = harness.events_of(CandleClosed)
    assert len(closed) == 1
    assert closed[0].open == 100.0
    assert closed[0].close == 105.0
    assert harness.state.latest_candle("BTCUSDT", Timeframe.M1) is not None


async def test_ticker_publishes_price_updated(harness: _Harness):
    now = utc_now()
    harness.collector.submit(
        Ticker(
            symbol="BTCUSDT",
            provider="binance",
            bid=99.0,
            ask=101.0,
            exchange_ts=now,
            local_ts=now,
        )
    )
    await harness.drain()

    assert len(harness.events_of(PriceUpdated)) == 1
    assert harness.state.ticker("BTCUSDT") is not None


async def test_book_snapshot_then_gap_requests_resync(harness: _Harness):
    def delta(
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        *,
        snapshot: bool = False,
        first: int = 0,
        last: int = 0,
    ) -> OrderBookDelta:
        return OrderBookDelta(
            symbol="BTCUSDT",
            provider="binance",
            bids=tuple(DepthLevel(price=p, size=s) for p, s in bids),
            asks=tuple(DepthLevel(price=p, size=s) for p, s in asks),
            is_snapshot=snapshot,
            first_sequence=first,
            last_sequence=last,
            exchange_ts=_TS,
            local_ts=_TS,
        )

    harness.collector.submit(delta([(100.0, 1.0)], [(101.0, 1.0)], snapshot=True, last=10))
    await harness.drain()
    assert len(harness.events_of(OrderBookUpdated)) == 1
    assert harness.state.book("BTCUSDT") is not None

    harness.collector.submit(delta([(100.0, 2.0)], [], first=50, last=50))  # hueco
    await harness.drain()
    assert len(harness.events_of(OrderBookResyncRequired)) == 1


async def test_flush_stale_candles_emits_close(harness: _Harness):
    harness.collector.submit(_trade(price=100.0, offset_s=0))
    await harness.drain()
    closed = await harness.collector.flush_stale_candles()  # now >> bucket end
    await harness.drain()
    assert closed == 1
    assert len(harness.events_of(CandleClosed)) == 1


async def test_tick_stream_listener_receives_trades(harness: _Harness):
    queue = harness.state.add_tick_listener("BTCUSDT")
    harness.collector.submit(_trade())
    await harness.drain()
    trade = queue.get_nowait()
    assert trade.symbol == "BTCUSDT"
    harness.state.remove_tick_listener(queue)

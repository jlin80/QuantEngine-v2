"""Integración: endpoints de mercado del dashboard."""

from datetime import UTC, datetime

import pytest
from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.market.cache import MarketCache
from app.market.models import (
    Candle,
    DepthLevel,
    OrderBook,
    Ticker,
    Timeframe,
    Trade,
    TradeSide,
)
from app.market.services import MarketDataService, MarketStateStore
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


@pytest.fixture()
def state() -> MarketStateStore:
    store = MarketStateStore()
    store.update_ticker(
        Ticker(
            symbol="BTCUSDT", provider="binance", bid=99.0, ask=101.0, exchange_ts=_TS, local_ts=_TS
        )
    )
    store.update_trade(
        Trade(
            symbol="BTCUSDT",
            provider="binance",
            price=100.0,
            size=1.0,
            side=TradeSide.BUY,
            exchange_ts=_TS,
            local_ts=_TS,
        )
    )
    store.update_book(
        OrderBook(
            symbol="BTCUSDT",
            provider="binance",
            bids=(DepthLevel(price=99.5, size=1.0),),
            asks=(DepthLevel(price=100.5, size=2.0),),
            exchange_ts=_TS,
            local_ts=_TS,
        )
    )
    store.update_candle(
        Candle(
            symbol="BTCUSDT",
            provider="binance",
            timeframe=Timeframe.M1,
            start=_TS,
            end=_TS.replace(minute=1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            closed=True,
        )
    )
    return store


@pytest.fixture()
def client(settings: Settings, state: MarketStateStore):
    container = Container()
    cache = MarketCache(CacheService(primary=None, fallback=InMemoryCache()))
    container.register_instance(MarketDataService, MarketDataService(state, cache))
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_price_endpoint(client: TestClient):
    response = client.get("/api/market/price/btcusdt")
    assert response.status_code == 200
    assert response.json() == {"symbol": "BTCUSDT", "price": 100.0}


def test_price_endpoint_404_for_unknown_symbol(client: TestClient):
    assert client.get("/api/market/price/DOGEUSDT").status_code == 404


def test_ticker_endpoint(client: TestClient):
    body = client.get("/api/market/ticker/BTCUSDT").json()
    assert body["bid"] == 99.0
    assert body["ask"] == 101.0
    assert body["spread"] == 2.0


def test_orderbook_endpoint(client: TestClient):
    body = client.get("/api/market/orderbook/BTCUSDT?levels=1").json()
    assert body["best_bid"] == 99.5
    assert body["best_ask"] == 100.5
    assert "imbalance" in body
    assert "microprice" in body


def test_candles_endpoint(client: TestClient):
    body = client.get("/api/market/candles/BTCUSDT?tf=1m").json()
    assert body["timeframe"] == "1m"
    assert len(body["candles"]) == 1
    assert body["candles"][0]["close"] == 100.5


def test_candles_endpoint_rejects_bad_timeframe(client: TestClient):
    assert client.get("/api/market/candles/BTCUSDT?tf=7x").status_code == 422


def test_trades_and_snapshot_endpoints(client: TestClient):
    trades = client.get("/api/market/trades/BTCUSDT").json()["trades"]
    assert len(trades) == 1
    snapshot = client.get("/api/market/snapshot/BTCUSDT").json()
    assert snapshot["ticker"]["bid"] == 99.0
    assert snapshot["candles"]["1m"]["close"] == 100.5


def test_symbols_endpoint(client: TestClient):
    body = client.get("/api/market/symbols").json()
    assert "BTCUSDT" in body["symbols"]


def test_market_endpoints_503_without_data_engine(settings: Settings):
    app = create_app(settings, Container())
    with TestClient(app) as client:
        assert client.get("/api/market/price/BTCUSDT").status_code == 503
        assert client.get("/api/market/status").status_code == 503

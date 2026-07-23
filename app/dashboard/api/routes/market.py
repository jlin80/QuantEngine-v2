"""Endpoints de observación del Data Engine."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.container import Container
from app.market.collector import TickCollector
from app.market.feed import MarketFeed
from app.market.models import Timeframe
from app.market.services import MarketDataService
from app.market.storage import MarketDataWriter

router = APIRouter(tags=["market"])


def _container(request: Request) -> Container:
    """App container or 503."""
    container: Container | None = request.app.state.container
    if container is None:
        raise HTTPException(status_code=503, detail="Container not available")
    return container


def _service(request: Request) -> MarketDataService:
    """MarketDataService or 503 (Data Engine deshabilitado)."""
    container = _container(request)
    if not container.contains(MarketDataService):
        raise HTTPException(status_code=503, detail="Data Engine not enabled")
    return container.resolve(MarketDataService)


@router.get("/market/status")
async def market_status(request: Request) -> dict[str, Any]:
    """Feed, conexiones, colas, storage y métricas en un solo snapshot."""
    container = _container(request)
    if not container.contains(MarketFeed):
        raise HTTPException(status_code=503, detail="Data Engine not enabled")
    feed = container.resolve(MarketFeed)
    collector = container.resolve(TickCollector)
    payload: dict[str, Any] = {
        "feed": feed.status(),
        "metrics": collector.metrics.to_dict(),
        "pipeline": {
            "processed": collector.processed,
            "queue_depth": collector.queue_depth,
        },
    }
    if container.contains(MarketDataWriter):
        payload["storage"] = container.resolve(MarketDataWriter).status()
    return payload


@router.get("/market/symbols")
async def market_symbols(request: Request) -> dict[str, Any]:
    """Símbolos con datos y su estado operativo."""
    service = _service(request)
    return {
        "symbols": {
            symbol: service.get_market_state(symbol).to_dict() for symbol in service.symbols
        }
    }


@router.get("/market/price/{symbol}")
async def market_price(symbol: str, request: Request) -> dict[str, Any]:
    """Último precio conocido de un símbolo."""
    service = _service(request)
    price = await service.get_last_price(symbol)
    if price is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")
    return {"symbol": symbol.upper(), "price": price}


@router.get("/market/ticker/{symbol}")
async def market_ticker(symbol: str, request: Request) -> dict[str, Any]:
    """Último best bid/ask de un símbolo."""
    ticker = _service(request).get_ticker(symbol)
    if ticker is None:
        raise HTTPException(status_code=404, detail=f"No ticker for {symbol}")
    return ticker.to_dict()


@router.get("/market/orderbook/{symbol}")
async def market_orderbook(symbol: str, request: Request, levels: int = 10) -> dict[str, Any]:
    """Último snapshot del libro con métricas derivadas."""
    book = _service(request).get_orderbook(symbol)
    if book is None:
        raise HTTPException(status_code=404, detail=f"No order book for {symbol}")
    return book.to_dict(levels)


@router.get("/market/candles/{symbol}")
async def market_candles(
    symbol: str, request: Request, tf: str = "1m", limit: int = 100
) -> dict[str, Any]:
    """Velas cerradas de una serie (ascendentes)."""
    try:
        timeframe = Timeframe(tf)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown timeframe '{tf}'") from exc
    candles = _service(request).get_candles(symbol, timeframe, limit)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe.value,
        "candles": [candle.to_dict() for candle in candles],
    }


@router.get("/market/trades/{symbol}")
async def market_trades(symbol: str, request: Request, limit: int = 50) -> dict[str, Any]:
    """Trades recientes de un símbolo (ascendentes)."""
    trades = _service(request).get_recent_trades(symbol, limit)
    return {
        "symbol": symbol.upper(),
        "trades": [trade.to_dict() for trade in trades],
    }


@router.get("/market/snapshot/{symbol}")
async def market_snapshot(symbol: str, request: Request) -> dict[str, Any]:
    """Fotografía compuesta del mercado para un símbolo."""
    return _service(request).get_market_snapshot(symbol).to_dict()

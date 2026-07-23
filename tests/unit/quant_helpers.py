"""Constructores compartidos para las pruebas del Quant Core (Fase 3).

Todo se construye con componentes reales: un `MarketDataService` sobre un
`MarketStateStore` poblado a mano — sin mocks de tipos, para que MyPy valide
los contratos igual que en producción.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.cache.memory import InMemoryCache
from app.cache.service import CacheService
from app.engine.models import Direction, MarketContext, StrategySignal
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
from app.utils.time import utc_now

TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def make_candles(
    closes: list[float],
    *,
    symbol: str = "BTCUSDT",
    timeframe: Timeframe = Timeframe.M1,
    range_pad: float = 0.2,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    vwaps: list[float] | None = None,
    start: datetime | None = None,
) -> list[Candle]:
    """Serie de velas cerradas consecutivas a partir de los cierres.

    Sin ``opens``, open = close (velas doji). Con ``opens`` y sin
    highs/lows explícitos, el rango envuelve el cuerpo con ``range_pad``.
    """
    step = timedelta(seconds=timeframe.seconds or 60)
    base = start if start is not None else TS
    candles: list[Candle] = []
    for i, close in enumerate(closes):
        open_ = opens[i] if opens is not None else close
        default_high = max(open_, close) + range_pad
        default_low = min(open_, close) - range_pad
        candles.append(
            Candle(
                symbol=symbol,
                provider="test",
                timeframe=timeframe,
                start=base + i * step,
                end=base + (i + 1) * step,
                open=open_,
                high=highs[i] if highs is not None else default_high,
                low=lows[i] if lows is not None else default_low,
                close=close,
                volume=volumes[i] if volumes is not None else 10.0,
                vwap=vwaps[i] if vwaps is not None else close,
                trades=1,
                closed=True,
            )
        )
    return candles


def make_ticker(symbol: str = "BTCUSDT", bid: float = 100.0, ask: float = 100.01) -> Ticker:
    """Ticker fresco (timestamps = ahora)."""
    now = utc_now()
    return Ticker(symbol=symbol, provider="test", bid=bid, ask=ask, exchange_ts=now, local_ts=now)


def make_trade(
    price: float = 100.0,
    size: float = 1.0,
    side: TradeSide = TradeSide.BUY,
    symbol: str = "BTCUSDT",
) -> Trade:
    """Trade fresco (timestamps = ahora)."""
    now = utc_now()
    return Trade(
        symbol=symbol,
        provider="test",
        price=price,
        size=size,
        side=side,
        exchange_ts=now,
        local_ts=now,
    )


def make_book(
    symbol: str = "BTCUSDT",
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> OrderBook:
    """Libro de órdenes con niveles explícitos."""
    now = utc_now()
    return OrderBook(
        symbol=symbol,
        provider="test",
        bids=tuple(DepthLevel(price=p, size=s) for p, s in (bids or [(100.0, 5.0)])),
        asks=tuple(DepthLevel(price=p, size=s) for p, s in (asks or [(100.1, 5.0)])),
        exchange_ts=now,
        local_ts=now,
    )


def make_market(
    *,
    candles: list[Candle] | None = None,
    trades: list[Trade] | None = None,
    ticker: Ticker | None = None,
    book: OrderBook | None = None,
) -> MarketDataService:
    """MarketDataService real sobre un estado poblado a mano (sin feed)."""
    state = MarketStateStore()
    for candle in candles or []:
        state.update_candle(candle)
    for trade in trades or []:
        state.update_trade(trade)
    if ticker is not None:
        state.update_ticker(ticker)
    if book is not None:
        state.update_book(book)
    cache = MarketCache(CacheService(primary=None, fallback=InMemoryCache()))
    return MarketDataService(state, cache, None)


def make_signal(
    strategy: str = "alpha",
    direction: Direction = Direction.LONG,
    score: float = 80.0,
    confidence: float = 0.8,
    symbol: str = "BTCUSDT",
    **kwargs: Any,
) -> StrategySignal:
    """Señal válida mínima (con razones: la explicabilidad es obligatoria)."""
    return StrategySignal(
        strategy_name=strategy,
        symbol=symbol,
        timestamp=kwargs.pop("timestamp", utc_now()),
        direction=direction,
        confidence=confidence,
        score=score,
        reasons=kwargs.pop("reasons", ("razón de prueba",)),
        **kwargs,
    )


def make_context(symbol: str = "BTCUSDT", **overrides: Any) -> MarketContext:
    """MarketContext artesanal para probar filtros/confianza en aislamiento."""
    return MarketContext(symbol=symbol, generated_at=utc_now(), **overrides)

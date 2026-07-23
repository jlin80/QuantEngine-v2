"""Cache de mercado sobre Redis (vía CacheService, con degradación).

Persiste el "último valor de todo" fuera del proceso: último precio, velas,
libro, funding, open interest y eventos recientes. Si Redis cae, el
CacheService degrada a memoria y el Data Engine sigue operando.
"""

import json
from typing import Any

from app.cache.service import CacheService
from app.market.models import Candle, FundingRate, OpenInterest, OrderBook, Ticker, Trade

_PREFIX = "mkt"
_RECENT_EVENTS_LIMIT = 100


class MarketCache:
    """Market-specific keyspace on top of the degradable cache service.

    Args:
        cache: Servicio de cache de Fase 1 (Redis → memoria).
        ttl_seconds: TTL por defecto de los valores (0 = sin TTL).
    """

    def __init__(self, cache: CacheService, *, ttl_seconds: float = 3600.0) -> None:
        self._cache = cache
        self._ttl = ttl_seconds or None

    @staticmethod
    def _key(*parts: str) -> str:
        """Build a namespaced cache key."""
        return ":".join((_PREFIX, *parts))

    async def _set_json(self, key: str, payload: dict[str, Any]) -> None:
        """Store a JSON document."""
        await self._cache.set(key, json.dumps(payload), ttl_seconds=self._ttl)

    async def _get_json(self, key: str) -> dict[str, Any] | None:
        """Read a JSON document (``None`` si no existe o está corrupto)."""
        raw = await self._cache.get(key)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------

    async def set_last_price(self, symbol: str, price: float, source: str) -> None:
        """Store the last known price for a symbol."""
        await self._set_json(self._key("price", symbol.upper()), {"price": price, "source": source})

    async def set_ticker(self, ticker: Ticker) -> None:
        """Store the latest ticker."""
        await self._set_json(self._key("ticker", ticker.symbol), ticker.to_dict())

    async def set_trade(self, trade: Trade) -> None:
        """Store the latest trade."""
        await self._set_json(self._key("trade", trade.symbol), trade.to_dict())

    async def set_candle(self, candle: Candle) -> None:
        """Store the latest closed candle for its timeframe."""
        key = self._key("candle", candle.symbol, candle.timeframe.value)
        await self._set_json(key, candle.to_dict())

    async def set_orderbook(self, book: OrderBook, *, levels: int = 10) -> None:
        """Store the latest order-book snapshot (truncated)."""
        await self._set_json(self._key("book", book.symbol), book.to_dict(levels))

    async def set_funding(self, funding: FundingRate) -> None:
        """Store the latest funding rate."""
        await self._set_json(self._key("funding", funding.symbol), funding.to_dict())

    async def set_open_interest(self, oi: OpenInterest) -> None:
        """Store the latest open interest."""
        await self._set_json(self._key("oi", oi.symbol), oi.to_dict())

    async def push_recent_event(self, event: dict[str, Any]) -> None:
        """Append to the rolling list of recent market events."""
        key = self._key("events", "recent")
        raw = await self._cache.get(key)
        events: list[dict[str, Any]] = []
        if raw is not None:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    events = parsed
            except json.JSONDecodeError:
                events = []
        events.append(event)
        await self._cache.set(
            key, json.dumps(events[-_RECENT_EVENTS_LIMIT:]), ttl_seconds=self._ttl
        )

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    async def get_last_price(self, symbol: str) -> float | None:
        """Last cached price for a symbol."""
        data = await self._get_json(self._key("price", symbol.upper()))
        if data is None:
            return None
        price = data.get("price")
        return float(price) if isinstance(price, int | float) else None

    async def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        """Last cached ticker document."""
        return await self._get_json(self._key("ticker", symbol.upper()))

    async def get_candle(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        """Last cached candle document for a timeframe."""
        return await self._get_json(self._key("candle", symbol.upper(), timeframe))

    async def get_orderbook(self, symbol: str) -> dict[str, Any] | None:
        """Last cached order-book document."""
        return await self._get_json(self._key("book", symbol.upper()))

    async def get_funding(self, symbol: str) -> dict[str, Any] | None:
        """Last cached funding document."""
        return await self._get_json(self._key("funding", symbol.upper()))

    async def get_open_interest(self, symbol: str) -> dict[str, Any] | None:
        """Last cached open-interest document."""
        return await self._get_json(self._key("oi", symbol.upper()))

    @property
    def degraded(self) -> bool:
        """Whether the underlying cache is running on the memory fallback."""
        return self._cache.degraded

    @property
    def backend_name(self) -> str:
        """Active cache backend name."""
        return self._cache.active_backend_name

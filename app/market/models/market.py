"""Modelos internos de datos de mercado.

Todo dato que entra al sistema termina convertido a uno de estos objetos —
inmutables, tipados y con doble timestamp (exchange + local, ambos UTC).
El formato original del exchange muere en el normalizador.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.market.models.enums import TradeSide
from app.market.models.timeframe import Timeframe


def _iso(moment: datetime | None) -> str | None:
    """Serialize a datetime to ISO-8601 (``None`` passthrough)."""
    return None if moment is None else moment.isoformat()


def latency_ms(exchange_ts: datetime, local_ts: datetime) -> float:
    """Compute reception latency in milliseconds (may be negative with skew).

    Args:
        exchange_ts: Timestamp reported by the exchange (UTC).
        local_ts: Local reception timestamp (UTC).

    Returns:
        ``local_ts - exchange_ts`` in milliseconds.
    """
    return (local_ts - exchange_ts).total_seconds() * 1000.0


@dataclass(frozen=True, kw_only=True, slots=True)
class Ticker:
    """Best bid/ask (y último precio) de un instrumento.

    Attributes:
        symbol: Símbolo interno (p. ej. ``BTCUSDT``, ``XAUUSD``).
        provider: Proveedor que originó el dato.
        bid: Mejor precio de compra.
        ask: Mejor precio de venta.
        bid_size: Cantidad en el best bid.
        ask_size: Cantidad en el best ask.
        last: Último precio operado (si el canal lo trae).
        mark_price: Mark price (derivados), si aplica.
        index_price: Index price (derivados), si aplica.
        exchange_ts: Timestamp del exchange (UTC).
        local_ts: Timestamp local de recepción (UTC).
    """

    symbol: str
    provider: str
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    last: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    exchange_ts: datetime
    local_ts: datetime

    @property
    def mid(self) -> float:
        """Punto medio entre bid y ask."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        """Spread absoluto (ask - bid)."""
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        """Spread en puntos básicos sobre el mid (0 si mid=0)."""
        mid = self.mid
        return (self.spread / mid) * 10_000.0 if mid else 0.0

    @property
    def latency_ms(self) -> float:
        """Latencia de recepción en milisegundos."""
        return latency_ms(self.exchange_ts, self.local_ts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "last": self.last,
            "mid": self.mid,
            "spread": self.spread,
            "spread_bps": self.spread_bps,
            "mark_price": self.mark_price,
            "index_price": self.index_price,
            "exchange_ts": _iso(self.exchange_ts),
            "local_ts": _iso(self.local_ts),
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Trade:
    """Un trade ejecutado en el exchange (tick de precio con volumen).

    Attributes:
        symbol: Símbolo interno.
        provider: Proveedor de origen.
        trade_id: Identificador del trade en el exchange ("" si no existe).
        price: Precio de ejecución.
        size: Cantidad ejecutada.
        side: Lado agresor.
        exchange_ts: Timestamp del exchange (UTC).
        local_ts: Timestamp local de recepción (UTC).
    """

    symbol: str
    provider: str
    trade_id: str = ""
    price: float
    size: float
    side: TradeSide = TradeSide.UNKNOWN
    exchange_ts: datetime
    local_ts: datetime

    @property
    def notional(self) -> float:
        """Valor nominal del trade (precio por cantidad)."""
        return self.price * self.size

    @property
    def latency_ms(self) -> float:
        """Latencia de recepción en milisegundos."""
        return latency_ms(self.exchange_ts, self.local_ts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "trade_id": self.trade_id,
            "price": self.price,
            "size": self.size,
            "side": self.side.value,
            "exchange_ts": _iso(self.exchange_ts),
            "local_ts": _iso(self.local_ts),
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class OHLCV:
    """Tupla OHLCV pura, sin metadatos."""

    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Candle:
    """Vela de un timeframe con metadatos de agregación.

    Attributes:
        symbol: Símbolo interno.
        provider: Proveedor de origen de los datos.
        timeframe: Timeframe de la vela.
        start: Inicio del bucket (UTC, inclusivo).
        end: Fin del bucket (UTC, exclusivo).
        open: Precio de apertura.
        high: Máximo.
        low: Mínimo.
        close: Cierre (último precio dentro del bucket).
        volume: Volumen total.
        buy_volume: Volumen agresor comprador.
        sell_volume: Volumen agresor vendedor.
        vwap: Precio promedio ponderado por volumen (0 si sin volumen).
        trades: Número de trades agregados.
        closed: Si la vela ya cerró (bucket completo).
        source: ``aggregated`` (propia) o ``provider`` (kline del exchange).
    """

    symbol: str
    provider: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    vwap: float = 0.0
    trades: int = 0
    closed: bool = False
    source: str = "aggregated"

    @property
    def ohlcv(self) -> OHLCV:
        """Vista OHLCV pura de la vela."""
        return OHLCV(
            open=self.open, high=self.high, low=self.low, close=self.close, volume=self.volume
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "timeframe": self.timeframe.value,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "vwap": self.vwap,
            "trades": self.trades,
            "closed": self.closed,
            "source": self.source,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class DepthLevel:
    """Un nivel del libro: precio y cantidad."""

    price: float
    size: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {"price": self.price, "size": self.size}


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBook:
    """Snapshot inmutable del libro de órdenes con métricas derivadas.

    Attributes:
        symbol: Símbolo interno.
        provider: Proveedor de origen.
        bids: Niveles de compra, ordenados de mejor a peor (precio desc).
        asks: Niveles de venta, ordenados de mejor a peor (precio asc).
        sequence: Último número de secuencia aplicado (0 si no aplica).
        exchange_ts: Timestamp del exchange (UTC).
        local_ts: Timestamp local (UTC).
    """

    symbol: str
    provider: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    sequence: int = 0
    exchange_ts: datetime
    local_ts: datetime

    @property
    def best_bid(self) -> DepthLevel | None:
        """Mejor nivel de compra (top of book)."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> DepthLevel | None:
        """Mejor nivel de venta (top of book)."""
        return self.asks[0] if self.asks else None

    @property
    def spread(self) -> float | None:
        """Spread absoluto entre best ask y best bid."""
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask.price - self.best_bid.price

    @property
    def mid(self) -> float | None:
        """Punto medio del top of book."""
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid.price + self.best_ask.price) / 2.0

    @property
    def microprice(self) -> float | None:
        """Microprice: mid ponderado por tamaños del top of book."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        total = bid.size + ask.size
        if total <= 0:
            return self.mid
        return (bid.price * ask.size + ask.price * bid.size) / total

    def depth(self, levels: int = 10) -> tuple[float, float]:
        """Sum the visible size on each side of the book.

        Args:
            levels: Cuántos niveles considerar por lado.

        Returns:
            Tupla ``(bid_depth, ask_depth)``.
        """
        bid_depth = sum(level.size for level in self.bids[:levels])
        ask_depth = sum(level.size for level in self.asks[:levels])
        return bid_depth, ask_depth

    def imbalance(self, levels: int = 10) -> float:
        """Order-flow imbalance in ``[-1, 1]`` (positivo = presión compradora).

        Args:
            levels: Cuántos niveles considerar por lado.

        Returns:
            ``(bid_depth - ask_depth) / (bid_depth + ask_depth)`` o 0.
        """
        bid_depth, ask_depth = self.depth(levels)
        total = bid_depth + ask_depth
        return (bid_depth - ask_depth) / total if total > 0 else 0.0

    def liquidity_within(self, pct: float) -> tuple[float, float]:
        """Liquidity (size) within ``±pct`` percent of the mid price.

        Args:
            pct: Distancia porcentual desde el mid (p. ej. ``0.5`` = ±0.5 %).

        Returns:
            Tupla ``(bid_liquidity, ask_liquidity)``; ``(0, 0)`` sin mid.
        """
        mid = self.mid
        if mid is None:
            return 0.0, 0.0
        low, high = mid * (1 - pct / 100.0), mid * (1 + pct / 100.0)
        bid_liq = sum(level.size for level in self.bids if level.price >= low)
        ask_liq = sum(level.size for level in self.asks if level.price <= high)
        return bid_liq, ask_liq

    def book_pressure(self, levels: int = 10) -> float:
        """Imbalance ponderado por cercanía al mid, en ``[-1, 1]``.

        Cada nivel pesa ``size / (1 + distancia_relativa_bps)``: la liquidez
        pegada al mid domina sobre la lejana.

        Args:
            levels: Cuántos niveles considerar por lado.

        Returns:
            Presión del libro (positivo = compradora) o 0 sin datos.
        """
        mid = self.mid
        if mid is None or mid <= 0:
            return 0.0

        def _weight(level: DepthLevel) -> float:
            distance_bps = abs(level.price - mid) / mid * 10_000.0
            return level.size / (1.0 + distance_bps)

        bid_pressure = sum(_weight(level) for level in self.bids[:levels])
        ask_pressure = sum(_weight(level) for level in self.asks[:levels])
        total = bid_pressure + ask_pressure
        return (bid_pressure - ask_pressure) / total if total > 0 else 0.0

    def to_dict(self, levels: int = 10) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary (truncated to ``levels``)."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "bids": [level.to_dict() for level in self.bids[:levels]],
            "asks": [level.to_dict() for level in self.asks[:levels]],
            "sequence": self.sequence,
            "best_bid": self.best_bid.price if self.best_bid else None,
            "best_ask": self.best_ask.price if self.best_ask else None,
            "spread": self.spread,
            "mid": self.mid,
            "microprice": self.microprice,
            "imbalance": self.imbalance(levels),
            "book_pressure": self.book_pressure(levels),
            "exchange_ts": _iso(self.exchange_ts),
            "local_ts": _iso(self.local_ts),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBookDelta:
    """Actualización cruda del libro (snapshot o incremental) ya normalizada.

    Attributes:
        symbol: Símbolo interno.
        provider: Proveedor de origen.
        bids: Niveles a aplicar en el lado comprador (size 0 = borrar nivel).
        asks: Niveles a aplicar en el lado vendedor (size 0 = borrar nivel).
        is_snapshot: Si sustituye el libro completo.
        first_sequence: Primera secuencia contenida (0 si no aplica).
        last_sequence: Última secuencia contenida (0 si no aplica).
        exchange_ts: Timestamp del exchange (UTC).
        local_ts: Timestamp local (UTC).
    """

    symbol: str
    provider: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    is_snapshot: bool = False
    first_sequence: int = 0
    last_sequence: int = 0
    exchange_ts: datetime
    local_ts: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class FundingRate:
    """Funding rate de un perpetuo."""

    symbol: str
    provider: str
    rate: float
    next_funding_ts: datetime | None = None
    exchange_ts: datetime
    local_ts: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "rate": self.rate,
            "next_funding_ts": _iso(self.next_funding_ts),
            "exchange_ts": _iso(self.exchange_ts),
            "local_ts": _iso(self.local_ts),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class OpenInterest:
    """Interés abierto de un derivado."""

    symbol: str
    provider: str
    contracts: float
    notional: float | None = None
    exchange_ts: datetime
    local_ts: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "contracts": self.contracts,
            "notional": self.notional,
            "exchange_ts": _iso(self.exchange_ts),
            "local_ts": _iso(self.local_ts),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Liquidation:
    """Liquidación forzada reportada por el exchange."""

    symbol: str
    provider: str
    side: TradeSide
    price: float
    size: float
    exchange_ts: datetime
    local_ts: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "side": self.side.value,
            "price": self.price,
            "size": self.size,
            "exchange_ts": _iso(self.exchange_ts),
            "local_ts": _iso(self.local_ts),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class MarketState:
    """Estado operativo del feed para un símbolo (¿está vivo el dato?).

    Attributes:
        symbol: Símbolo interno.
        provider: Proveedor asignado.
        subscribed: Si existe una suscripción activa.
        connected: Si la conexión del proveedor está establecida.
        ticks_received: Trades recibidos desde el arranque.
        last_update: Último dato recibido (UTC) o ``None``.
        staleness_seconds: Segundos desde el último dato (``None`` sin datos).
    """

    symbol: str
    provider: str
    subscribed: bool
    connected: bool
    ticks_received: int
    last_update: datetime | None
    staleness_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "subscribed": self.subscribed,
            "connected": self.connected,
            "ticks_received": self.ticks_received,
            "last_update": _iso(self.last_update),
            "staleness_seconds": self.staleness_seconds,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class MarketSnapshot:
    """Fotografía compuesta del mercado para un símbolo.

    Attributes:
        symbol: Símbolo interno.
        provider: Proveedor de origen.
        generated_at: Momento de generación (UTC).
        ticker: Último ticker conocido.
        last_trade: Último trade conocido.
        orderbook: Último snapshot del libro.
        candles: Última vela cerrada por timeframe.
        funding: Último funding rate.
        open_interest: Último open interest.
    """

    symbol: str
    provider: str
    generated_at: datetime
    ticker: Ticker | None = None
    last_trade: Trade | None = None
    orderbook: OrderBook | None = None
    candles: dict[str, Candle] = field(default_factory=dict)
    funding: FundingRate | None = None
    open_interest: OpenInterest | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "generated_at": _iso(self.generated_at),
            "ticker": self.ticker.to_dict() if self.ticker else None,
            "last_trade": self.last_trade.to_dict() if self.last_trade else None,
            "orderbook": self.orderbook.to_dict() if self.orderbook else None,
            "candles": {tf: candle.to_dict() for tf, candle in self.candles.items()},
            "funding": self.funding.to_dict() if self.funding else None,
            "open_interest": self.open_interest.to_dict() if self.open_interest else None,
        }

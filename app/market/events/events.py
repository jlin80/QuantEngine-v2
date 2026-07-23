"""Eventos del Data Engine.

Regla de diseño: los eventos llevan **solo campos primitivos** (JSON-safe sin
conversión) — así el WebSocket del dashboard los retransmite tal cual. El
objeto completo se obtiene del
:class:`~app.market.services.market_data.MarketDataService`, nunca del evento.
"""

from dataclasses import dataclass

from app.core.events.base import Event


@dataclass(frozen=True, kw_only=True, slots=True)
class NewTick(Event):
    """Un tick (trade) validado entró al sistema."""

    symbol: str
    provider: str
    price: float
    size: float
    side: str
    exchange_ts: str
    latency_ms: float


@dataclass(frozen=True, kw_only=True, slots=True)
class TradeReceived(Event):
    """Trade normalizado y aceptado (alias semántico de dominio)."""

    symbol: str
    provider: str
    trade_id: str
    price: float
    size: float
    side: str
    exchange_ts: str


@dataclass(frozen=True, kw_only=True, slots=True)
class TickerUpdated(Event):
    """Best bid/ask actualizado."""

    symbol: str
    provider: str
    bid: float
    ask: float
    spread: float
    mid: float
    latency_ms: float


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBookUpdated(Event):
    """El libro de órdenes cambió (se publica el top of book)."""

    symbol: str
    provider: str
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    mid: float | None
    imbalance: float
    sequence: int


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBookResyncRequired(Event):
    """El libro perdió secuencia y debe reconstruirse vía REST."""

    symbol: str
    provider: str
    reason: str = "sequence_gap"


@dataclass(frozen=True, kw_only=True, slots=True)
class CandleClosed(Event):
    """Una vela cerró (agregada localmente o entregada por el proveedor)."""

    symbol: str
    provider: str
    timeframe: str
    start: str
    end: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    trades: int
    candle_source: str = "aggregated"


@dataclass(frozen=True, kw_only=True, slots=True)
class FundingUpdated(Event):
    """Funding rate actualizado."""

    symbol: str
    provider: str
    rate: float


@dataclass(frozen=True, kw_only=True, slots=True)
class OpenInterestUpdated(Event):
    """Open interest actualizado."""

    symbol: str
    provider: str
    contracts: float


@dataclass(frozen=True, kw_only=True, slots=True)
class LiquidationReceived(Event):
    """Liquidación forzada recibida."""

    symbol: str
    provider: str
    side: str
    price: float
    size: float


@dataclass(frozen=True, kw_only=True, slots=True)
class DataQualityAlert(Event):
    """Un dato fue descartado o marcado por el validador."""

    symbol: str
    provider: str
    issue: str
    detail: str
    discarded: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class ConnectionRecovered(Event):
    """Una conexión de streaming se recuperó tras una caída."""

    provider: str
    connection: str
    attempts: int
    downtime_seconds: float


@dataclass(frozen=True, kw_only=True, slots=True)
class FeedSubscribed(Event):
    """El feed activó una suscripción para un símbolo."""

    symbol: str
    provider: str
    channels: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class FeedUnsubscribed(Event):
    """El feed retiró la suscripción de un símbolo."""

    symbol: str
    provider: str

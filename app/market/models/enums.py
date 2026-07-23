"""Enumeraciones del dominio de datos de mercado."""

import enum


class TradeSide(enum.StrEnum):
    """Lado agresor de un trade."""

    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class ChannelType(enum.StrEnum):
    """Canales de datos que un proveedor puede servir."""

    TICKER = "ticker"
    TRADES = "trades"
    ORDERBOOK = "orderbook"
    CANDLES = "candles"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    LIQUIDATIONS = "liquidations"
    MARK_PRICE = "mark_price"


class ConnectionState(enum.StrEnum):
    """Estados de una conexión de streaming."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


class QualityIssueType(enum.StrEnum):
    """Clasificación de problemas de calidad de datos."""

    NEGATIVE_PRICE = "negative_price"
    NEGATIVE_SIZE = "negative_size"
    INVALID_TIMESTAMP = "invalid_timestamp"
    FUTURE_TIMESTAMP = "future_timestamp"
    STALE_TIMESTAMP = "stale_timestamp"
    OUT_OF_ORDER = "out_of_order"
    DUPLICATE = "duplicate"
    PRICE_GAP = "price_gap"
    IMPOSSIBLE_VOLUME = "impossible_volume"
    MALFORMED_CANDLE = "malformed_candle"
    CROSSED_BOOK = "crossed_book"
    SEQUENCE_GAP = "sequence_gap"

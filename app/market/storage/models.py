"""Modelos ORM de datos de mercado (primeras tablas del sistema).

``market_ticks`` guarda cada trade recibido; ``market_candles`` las velas
cerradas. Índices pensados para la consulta típica: símbolo + rango temporal.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TickRow(Base):
    """Un trade persistido (tick con volumen)."""

    __tablename__ = "market_ticks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    local_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_market_ticks_symbol_ts", "symbol", "exchange_ts"),)


class CandleRow(Base):
    """Una vela cerrada persistida."""

    __tablename__ = "market_candles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    buy_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sell_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vwap: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="aggregated", nullable=False)

    __table_args__ = (
        Index(
            "ix_market_candles_series",
            "symbol",
            "provider",
            "timeframe",
            "start",
            unique=True,
        ),
    )

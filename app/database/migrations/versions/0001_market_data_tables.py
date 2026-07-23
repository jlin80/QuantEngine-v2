"""Fase 2: tablas de datos de mercado (ticks y velas).

Revision ID: 0001
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create market_ticks and market_candles."""
    op.create_table(
        "market_ticks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("size", sa.Float(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("exchange_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_ticks")),
    )
    op.create_index("ix_market_ticks_symbol_ts", "market_ticks", ["symbol", "exchange_ts"])

    op.create_table(
        "market_candles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("buy_volume", sa.Float(), nullable=False),
        sa.Column("sell_volume", sa.Float(), nullable=False),
        sa.Column("vwap", sa.Float(), nullable=False),
        sa.Column("trades", sa.Integer(), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_candles")),
    )
    op.create_index(
        "ix_market_candles_series",
        "market_candles",
        ["symbol", "provider", "timeframe", "start"],
        unique=True,
    )


def downgrade() -> None:
    """Drop market tables."""
    op.drop_index("ix_market_candles_series", table_name="market_candles")
    op.drop_table("market_candles")
    op.drop_index("ix_market_ticks_symbol_ts", table_name="market_ticks")
    op.drop_table("market_ticks")

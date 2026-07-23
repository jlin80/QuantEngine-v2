"""Fase 3: historial de señales y decisiones del Quant Core.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create strategy_signals and engine_decisions."""
    op.create_table(
        "strategy_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.String(length=32), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reasons", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_signals")),
        sa.UniqueConstraint("signal_id", name=op.f("uq_strategy_signals_signal_id")),
    )
    op.create_index("ix_strategy_signals_symbol_ts", "strategy_signals", ["symbol", "created_at"])

    op.create_table(
        "engine_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("agreement", sa.Float(), nullable=False),
        sa.Column("regime", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engine_decisions")),
        sa.UniqueConstraint("decision_id", name=op.f("uq_engine_decisions_decision_id")),
    )
    op.create_index("ix_engine_decisions_symbol_ts", "engine_decisions", ["symbol", "created_at"])


def downgrade() -> None:
    """Drop engine history tables."""
    op.drop_index("ix_engine_decisions_symbol_ts", table_name="engine_decisions")
    op.drop_table("engine_decisions")
    op.drop_index("ix_strategy_signals_symbol_ts", table_name="strategy_signals")
    op.drop_table("strategy_signals")

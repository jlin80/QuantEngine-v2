"""Tareas periódicas del Data Engine."""

from app.market.scheduler.jobs import register_market_jobs

__all__ = ["register_market_jobs"]

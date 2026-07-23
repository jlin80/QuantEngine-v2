"""Tareas periódicas del Data Engine (sobre el scheduler de Fase 1).

Jobs registrados:
    * ``market_flush_candles`` — cierra velas expiradas sin trades.
    * ``market_connection_watch`` — vigila silencio por símbolo y fuerza
      resincronización del libro cuando el dato se enfría.
    * ``market_clock_drift`` — mide drift de reloj contra cada exchange.
    * ``market_metrics_snapshot`` — publica métricas del feed a la cache.
"""

import json
import logging

from app.cache.service import CacheService
from app.market.collector import TickCollector
from app.market.feed import MarketFeed
from app.scheduler.scheduler import AsyncScheduler

_log = logging.getLogger("app.market.jobs")


def register_market_jobs(
    scheduler: AsyncScheduler,
    feed: MarketFeed,
    collector: TickCollector,
    cache: CacheService,
) -> None:
    """Register every periodic Data Engine job.

    Args:
        scheduler: Scheduler interno del motor.
        feed: Feed de mercado.
        collector: Pipeline (dueño del agregador y las métricas).
        cache: Cache del sistema (para snapshots de métricas).
    """

    async def flush_candles() -> None:
        closed = await collector.flush_stale_candles()
        if closed:
            _log.debug("Flushed %d stale candles", closed)

    async def connection_watch() -> None:
        for symbol in feed.subscriptions:
            if not feed.is_symbol_connected(symbol):
                _log.warning("Symbol %s has no live connection", symbol)

    async def clock_drift() -> None:
        drifts = await feed.measure_clock_drift()
        for provider, drift in drifts.items():
            if abs(drift) > 1.0:
                _log.warning("Clock drift vs %s: %.3fs", provider, drift)
        if drifts:
            await cache.set("mkt:clock_drift", json.dumps(drifts), ttl_seconds=3600)

    async def metrics_snapshot() -> None:
        await cache.set(
            "mkt:metrics",
            json.dumps(collector.metrics.to_dict()),
            ttl_seconds=300,
        )

    scheduler.add_job("market_flush_candles", flush_candles, interval_seconds=1.0)
    scheduler.add_job("market_connection_watch", connection_watch, interval_seconds=30.0)
    scheduler.add_job("market_clock_drift", clock_drift, interval_seconds=300.0)
    scheduler.add_job("market_metrics_snapshot", metrics_snapshot, interval_seconds=15.0)

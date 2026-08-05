"""Collector: la tubería central del Data Engine.

Los proveedores empujan objetos normalizados con ``submit()`` (no bloqueante,
seguro desde cualquier callback). Un worker asíncrono drena la cola y ejecuta
el pipeline: validar → actualizar estado → agregar velas → reconstruir libro
→ cachear → persistir → publicar eventos.
"""

import asyncio
import contextlib
import logging
from typing import Any, Protocol

from app.core.events.bus import EventBus
from app.core.events.events import PriceUpdated
from app.core.exceptions import EventBusError
from app.core.lifecycle import Service
from app.market.aggregator import CandleAggregator
from app.market.cache import MarketCache
from app.market.events import (
    CandleClosed,
    DataQualityAlert,
    FundingUpdated,
    LiquidationReceived,
    NewTick,
    OpenInterestUpdated,
    OrderBookResyncRequired,
    OrderBookUpdated,
    TickerUpdated,
    TradeReceived,
)
from app.market.models import (
    Candle,
    FundingRate,
    Liquidation,
    MarketObject,
    OpenInterest,
    OrderBook,
    OrderBookDelta,
    Ticker,
    Trade,
)
from app.market.services.orderbook_manager import OrderBookManager
from app.market.services.state import MarketStateStore
from app.market.storage.writer import MarketDataWriter
from app.market.stream.metrics import FeedMetrics
from app.market.validator import DataValidator
from app.utils.time import utc_now


class BookObserver(Protocol):
    """Observador de la dinámica del libro (lo implementa el Bloque 3).

    Se declara aquí como Protocol para que la capa de mercado no importe el
    motor de microestructura: el collector sólo sabe que hay algo a quien
    avisar, y el composition root decide quién es.
    """

    def observe_delta(self, delta: OrderBookDelta, book: OrderBook | None = None) -> None:
        """Record one incremental book update."""

    def observe_trade(self, trade: Trade) -> None:
        """Record one executed trade."""


class TickCollector(Service):
    """Queue-decoupled processing pipeline for normalized market objects.

    Args:
        bus: Event Bus del sistema.
        validator: Validador de calidad de datos.
        aggregator: Agregador de velas.
        books: Reconstructor de order books.
        state: Estado vivo en memoria.
        cache: Cache de mercado (Redis degradable).
        writer: Persistencia batched (``None`` = sin persistencia).
        metrics: Métricas del feed.
        queue_size: Capacidad de la cola de entrada.
        book_observer: Observador de microestructura (Bloque 3), opcional.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        validator: DataValidator,
        aggregator: CandleAggregator,
        books: OrderBookManager,
        state: MarketStateStore,
        cache: MarketCache,
        writer: MarketDataWriter | None = None,
        metrics: FeedMetrics | None = None,
        queue_size: int = 100_000,
        aggregate_from_ticks: bool = False,
        book_observer: "BookObserver | None" = None,
    ) -> None:
        super().__init__("collector")
        self._bus = bus
        self._validator = validator
        self._aggregator = aggregator
        self._aggregate_from_ticks = aggregate_from_ticks
        self._books = books
        # Observador de microestructura (Bloque 3). Opcional y en el camino
        # caliente: sólo se le pasan objetos ya validados, y lo que hace es
        # aritmética sobre dicts. Si no está cableado, cero coste.
        self._book_observer = book_observer
        self._market_state = state
        self._cache = cache
        self._writer = writer
        self._metrics = metrics or FeedMetrics()
        self._queue: asyncio.Queue[MarketObject] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._processed = 0
        self._log = logging.getLogger("app.market.collector")

    @property
    def metrics(self) -> FeedMetrics:
        """Feed metrics owned by the pipeline."""
        return self._metrics

    @property
    def processed(self) -> int:
        """Objects fully processed since startup."""
        return self._processed

    @property
    def queue_depth(self) -> int:
        """Objects currently waiting in the intake queue."""
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    def submit(self, obj: MarketObject) -> None:
        """Enqueue a normalized object (never blocks the provider).

        Si la cola está llena el objeto se descarta y se contabiliza: la
        presión de memoria jamás debe congelar la conexión.
        """
        self._metrics.ws_messages.hit()
        try:
            self._queue.put_nowait(obj)
        except asyncio.QueueFull:
            self._metrics.dropped += 1
            self._log.warning("Collector queue full — object dropped")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _on_start(self) -> None:
        self._worker = asyncio.create_task(self._drain_loop(), name="collector-worker")

    async def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def healthcheck(self) -> bool:
        """Healthy while the worker is alive."""
        return self.is_running and self._worker is not None and not self._worker.done()

    async def _drain_loop(self) -> None:
        """Consume the queue forever, isolating per-object failures."""
        while True:
            obj = await self._queue.get()
            try:
                await self._process(obj)
                self._processed += 1
            except Exception:
                self._metrics.errors += 1
                self._log.exception("Pipeline failed for %s", type(obj).__name__)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def _process(self, obj: MarketObject) -> None:
        """Route one object through validation and the full pipeline."""
        if isinstance(obj, Trade):
            await self._process_trade(obj)
        elif isinstance(obj, Ticker):
            await self._process_ticker(obj)
        elif isinstance(obj, OrderBookDelta):
            await self._process_book_delta(obj)
        elif isinstance(obj, Candle):
            await self._process_candle(obj)
        elif isinstance(obj, FundingRate):
            await self._process_funding(obj)
        elif isinstance(obj, OpenInterest):
            await self._process_open_interest(obj)
        elif isinstance(obj, Liquidation):
            await self._process_liquidation(obj)

    async def _reject(self, obj_symbol: str, provider: str, issues: Any) -> None:
        """Publish quality alerts for discarded data."""
        self._metrics.rejected += 1
        for issue in issues:
            await self._publish(
                DataQualityAlert(
                    source="collector",
                    symbol=obj_symbol,
                    provider=provider,
                    issue=issue.issue.value,
                    detail=issue.detail,
                    discarded=issue.discard,
                )
            )

    async def _process_trade(self, trade: Trade) -> None:
        """Trades: validate → state → candles → cache → storage → events."""
        issues = self._validator.validate_trade(trade)
        if self._validator.should_discard(issues):
            await self._reject(trade.symbol, trade.provider, issues)
            return
        self._metrics.tick(trade.symbol)
        self._metrics.data_latency.observe(trade.latency_ms)
        self._market_state.update_trade(trade)
        if self._book_observer is not None:
            self._book_observer.observe_trade(trade)
        if self._writer is not None:
            self._writer.add_trade(trade)
        await self._cache.set_last_price(trade.symbol, trade.price, "trade")
        await self._cache.set_trade(trade)

        for candle in self._aggregator.add_trade(trade):
            await self._emit_closed_candle(candle)

        await self._publish(
            NewTick(
                source="collector",
                symbol=trade.symbol,
                provider=trade.provider,
                price=trade.price,
                size=trade.size,
                side=trade.side.value,
                exchange_ts=trade.exchange_ts.isoformat(),
                latency_ms=trade.latency_ms,
            )
        )
        await self._publish(
            TradeReceived(
                source="collector",
                symbol=trade.symbol,
                provider=trade.provider,
                trade_id=trade.trade_id,
                price=trade.price,
                size=trade.size,
                side=trade.side.value,
                exchange_ts=trade.exchange_ts.isoformat(),
            )
        )

    async def _process_ticker(self, ticker: Ticker) -> None:
        """Tickers: validate → state → cache → events (incl. PriceUpdated)."""
        issues = self._validator.validate_ticker(ticker)
        if self._validator.should_discard(issues):
            await self._reject(ticker.symbol, ticker.provider, issues)
            return
        self._metrics.data_latency.observe(ticker.latency_ms)
        self._market_state.update_ticker(ticker)
        await self._cache.set_ticker(ticker)
        # Feeds sin tape de trades (MT5): la vela se construye desde los quotes.
        if self._aggregate_from_ticks:
            for candle in self._aggregator.add_ticker(ticker):
                await self._emit_closed_candle(candle)
        await self._publish(
            TickerUpdated(
                source="collector",
                symbol=ticker.symbol,
                provider=ticker.provider,
                bid=ticker.bid,
                ask=ticker.ask,
                spread=ticker.spread,
                mid=ticker.mid,
                latency_ms=ticker.latency_ms,
            )
        )
        await self._publish(
            PriceUpdated(
                source="collector",
                symbol=ticker.symbol,
                bid=ticker.bid,
                ask=ticker.ask,
                timestamp=ticker.exchange_ts,
            )
        )

    async def _process_book_delta(self, delta: OrderBookDelta) -> None:
        """Book deltas: validate → rebuild → state → cache → events."""
        issues = self._validator.validate_book_delta(delta)
        if self._validator.should_discard(issues):
            await self._reject(delta.symbol, delta.provider, issues)
            return
        book, resync_needed = self._books.apply(delta)
        if resync_needed:
            await self._publish(
                OrderBookResyncRequired(
                    source="collector", symbol=delta.symbol, provider=delta.provider
                )
            )
            return
        if book is None:
            return
        if self._book_observer is not None:
            self._book_observer.observe_delta(delta, book)
        self._market_state.update_book(book)
        await self._cache.set_orderbook(book)
        await self._publish(_book_event(book))

    async def _process_candle(self, candle: Candle) -> None:
        """Provider candles: validate → state/cache/storage si cerró."""
        issues = self._validator.validate_candle(candle)
        if self._validator.should_discard(issues):
            await self._reject(candle.symbol, candle.provider, issues)
            return
        if candle.closed:
            await self._emit_closed_candle(candle)

    async def _emit_closed_candle(self, candle: Candle) -> None:
        """Shared path for every closed candle (aggregated or provider)."""
        self._market_state.update_candle(candle)
        await self._cache.set_candle(candle)
        if self._writer is not None:
            self._writer.add_candles([candle])
        await self._publish(
            CandleClosed(
                source="collector",
                symbol=candle.symbol,
                provider=candle.provider,
                timeframe=candle.timeframe.value,
                start=candle.start.isoformat(),
                end=candle.end.isoformat(),
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                vwap=candle.vwap,
                trades=candle.trades,
                candle_source=candle.source,
            )
        )

    async def _process_funding(self, funding: FundingRate) -> None:
        """Funding: validate → state → cache → event."""
        issues = self._validator.validate_funding(funding)
        if self._validator.should_discard(issues):
            await self._reject(funding.symbol, funding.provider, issues)
            return
        self._market_state.update_funding(funding)
        await self._cache.set_funding(funding)
        await self._publish(
            FundingUpdated(
                source="collector",
                symbol=funding.symbol,
                provider=funding.provider,
                rate=funding.rate,
            )
        )

    async def _process_open_interest(self, oi: OpenInterest) -> None:
        """Open interest: validate → state → cache → event."""
        issues = self._validator.validate_open_interest(oi)
        if self._validator.should_discard(issues):
            await self._reject(oi.symbol, oi.provider, issues)
            return
        self._market_state.update_open_interest(oi)
        await self._cache.set_open_interest(oi)
        await self._publish(
            OpenInterestUpdated(
                source="collector",
                symbol=oi.symbol,
                provider=oi.provider,
                contracts=oi.contracts,
            )
        )

    async def _process_liquidation(self, liq: Liquidation) -> None:
        """Liquidations: validate → event."""
        issues = self._validator.validate_liquidation(liq)
        if self._validator.should_discard(issues):
            await self._reject(liq.symbol, liq.provider, issues)
            return
        await self._publish(
            LiquidationReceived(
                source="collector",
                symbol=liq.symbol,
                provider=liq.provider,
                side=liq.side.value,
                price=liq.price,
                size=liq.size,
            )
        )

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    async def _publish(self, event: Any) -> None:
        """Publish to the bus, tolerating bus saturation."""
        try:
            await self._bus.publish(event)
        except EventBusError as exc:
            self._metrics.errors += 1
            self._log.warning("Event publish failed: %s", exc)

    async def prime_candles(self, candles: list[Candle]) -> int:
        """Seed history WITHOUT emitting events (warmup en la suscripción).

        Rellena estado y cache con velas cerradas ya conocidas del proveedor
        para que las estrategias tengan su ventana mínima desde el arranque. NO
        publica ``CandleClosed``: disparar la evaluación sobre velas históricas
        produciría señales sobre datos viejos.

        Args:
            candles: Velas (se ignoran las no cerradas).

        Returns:
            Cuántas velas se sembraron.
        """
        primed = 0
        for candle in candles:
            if not candle.closed:
                continue
            self._market_state.update_candle(candle)
            await self._cache.set_candle(candle)
            primed += 1
        return primed

    async def flush_stale_candles(self) -> int:
        """Close expired buckets without trades (scheduler job).

        Returns:
            Cuántas velas cerraron por expiración.
        """
        closed = self._aggregator.flush_stale(utc_now())
        for candle in closed:
            await self._emit_closed_candle(candle)
        return len(closed)


def _book_event(book: OrderBook) -> OrderBookUpdated:
    """Build the OrderBookUpdated event from a snapshot."""
    return OrderBookUpdated(
        source="collector",
        symbol=book.symbol,
        provider=book.provider,
        best_bid=book.best_bid.price if book.best_bid else None,
        best_ask=book.best_ask.price if book.best_ask else None,
        spread=book.spread,
        mid=book.mid,
        imbalance=book.imbalance(),
        sequence=book.sequence,
    )

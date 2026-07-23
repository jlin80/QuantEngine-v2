"""Reconstrucción del libro de órdenes por snapshots + deltas incrementales.

Cada símbolo tiene un :class:`OrderBookEngine` que mantiene el libro vivo y
detecta huecos de secuencia. Ante un hueco, el libro queda inválido hasta que
llegue un snapshot nuevo (el feed lo pide por REST).
"""

import logging
from datetime import datetime

from app.market.models import DepthLevel, OrderBook, OrderBookDelta


class OrderBookEngine:
    """Live order book for one symbol (mutable, produces immutable snapshots).

    Args:
        symbol: Símbolo interno.
        provider: Proveedor de origen.
        max_depth: Niveles máximos a retener por lado.
    """

    def __init__(self, symbol: str, provider: str, *, max_depth: int = 200) -> None:
        self._symbol = symbol
        self._provider = provider
        self._max_depth = max_depth
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self._sequence = 0
        self._synced = False
        self._exchange_ts: datetime | None = None
        self._local_ts: datetime | None = None
        self._updates_applied = 0
        self._log = logging.getLogger("app.market.orderbook")

    @property
    def synced(self) -> bool:
        """Whether the book is currently valid (snapshot aplicado, sin huecos)."""
        return self._synced

    @property
    def sequence(self) -> int:
        """Última secuencia aplicada."""
        return self._sequence

    @property
    def updates_applied(self) -> int:
        """Cuántos deltas se han aplicado desde el arranque."""
        return self._updates_applied

    def apply(self, delta: OrderBookDelta) -> OrderBook | None:
        """Apply a snapshot or incremental update.

        Args:
            delta: Actualización normalizada.

        Returns:
            El snapshot inmutable resultante, o ``None`` si el libro quedó
            desincronizado (hueco de secuencia) o aún no hay snapshot base.
        """
        if delta.is_snapshot:
            self._bids = {level.price: level.size for level in delta.bids if level.size > 0}
            self._asks = {level.price: level.size for level in delta.asks if level.size > 0}
            self._sequence = delta.last_sequence
            self._synced = True
        else:
            if not self._synced:
                return None
            if self._has_gap(delta):
                self._log.warning(
                    "Order book gap for %s: last=%d, delta=[%d, %d] — resync required",
                    self._symbol,
                    self._sequence,
                    delta.first_sequence,
                    delta.last_sequence,
                )
                self._synced = False
                return None
            # Deltas ya vistos (secuencia vieja) se ignoran sin invalidar.
            if 0 < delta.last_sequence <= self._sequence:
                return None
            self._apply_side(self._bids, delta.bids)
            self._apply_side(self._asks, delta.asks)
            if delta.last_sequence:
                self._sequence = delta.last_sequence
        self._exchange_ts = delta.exchange_ts
        self._local_ts = delta.local_ts
        self._updates_applied += 1
        self._trim()
        return self.snapshot()

    def _has_gap(self, delta: OrderBookDelta) -> bool:
        """Detect a sequence hole between the book and an incoming delta."""
        if delta.first_sequence == 0 or self._sequence == 0:
            return False
        return delta.first_sequence > self._sequence + 1

    @staticmethod
    def _apply_side(side: dict[float, float], levels: tuple[DepthLevel, ...]) -> None:
        """Apply level updates to one side (size 0 elimina el nivel)."""
        for level in levels:
            if level.size <= 0:
                side.pop(level.price, None)
            else:
                side[level.price] = level.size

    def _trim(self) -> None:
        """Keep only the best ``max_depth`` levels per side."""
        if len(self._bids) > self._max_depth:
            for price in sorted(self._bids, reverse=True)[self._max_depth :]:
                del self._bids[price]
        if len(self._asks) > self._max_depth:
            for price in sorted(self._asks)[self._max_depth :]:
                del self._asks[price]

    def snapshot(self) -> OrderBook | None:
        """Produce the immutable snapshot of the current book state."""
        if not self._synced or self._exchange_ts is None or self._local_ts is None:
            return None
        bids = tuple(
            DepthLevel(price=price, size=self._bids[price])
            for price in sorted(self._bids, reverse=True)
        )
        asks = tuple(
            DepthLevel(price=price, size=self._asks[price]) for price in sorted(self._asks)
        )
        return OrderBook(
            symbol=self._symbol,
            provider=self._provider,
            bids=bids,
            asks=asks,
            sequence=self._sequence,
            exchange_ts=self._exchange_ts,
            local_ts=self._local_ts,
        )


class OrderBookManager:
    """Registry of per-symbol order-book engines.

    Args:
        max_depth: Niveles máximos por lado en cada libro.
    """

    def __init__(self, *, max_depth: int = 200) -> None:
        self._max_depth = max_depth
        self._engines: dict[str, OrderBookEngine] = {}

    def engine(self, symbol: str, provider: str) -> OrderBookEngine:
        """Get (or create) the engine for a symbol."""
        engine = self._engines.get(symbol)
        if engine is None:
            engine = OrderBookEngine(symbol, provider, max_depth=self._max_depth)
            self._engines[symbol] = engine
        return engine

    def apply(self, delta: OrderBookDelta) -> tuple[OrderBook | None, bool]:
        """Apply a delta and report whether a resync is needed.

        Args:
            delta: Actualización normalizada.

        Returns:
            ``(snapshot, resync_needed)`` — snapshot es ``None`` si el libro
            no está listo; ``resync_needed`` indica hueco de secuencia.
        """
        engine = self.engine(delta.symbol, delta.provider)
        was_synced = engine.synced
        book = engine.apply(delta)
        resync_needed = was_synced and not engine.synced
        return book, resync_needed

    def status(self) -> dict[str, dict[str, object]]:
        """Diagnostic snapshot per symbol (synced, secuencia, updates)."""
        return {
            symbol: {
                "synced": engine.synced,
                "sequence": engine.sequence,
                "updates_applied": engine.updates_applied,
            }
            for symbol, engine in self._engines.items()
        }

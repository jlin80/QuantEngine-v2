"""Microstructure Engine (Bloque 3) — lo que pasa dentro del libro.

Se alimenta de las actualizaciones **incrementales** del libro, no de
snapshots: la diferencia entre "el nivel bajó de 10 a 4" por cancelación y por
ejecución sólo existe si se ven los deltas y las operaciones por separado. Con
snapshots periódicos ambos casos son la misma resta.

**Dependencia dura, declarada por delante.** Todo esto exige un proveedor que
publique libro incremental. MT5 —el bróker de la demo— no lo publica: no expone
``ORDERBOOK`` y nunca emite ``Trade`` (medido y escrito en
``docs/orderflow_nativo.md``). Con MT5 el motor reporta ``observable=False`` con
su motivo y **todas las métricas en ``None``**. Es la decisión central del
módulo: un ``queue_imbalance`` de 0.0 fabricado es indistinguible de un libro
perfectamente equilibrado, y alimentaría al Decision Engine con una lectura de
mercado que nadie tomó.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config.settings import QuantMicrostructureSettings
from app.engine.microstructure.models import MicrostructureSnapshot
from app.market.models.market import OrderBook, OrderBookDelta, Trade
from app.utils.time import utc_now


@dataclass(kw_only=True, slots=True)
class _Event:
    """Un cambio de tamaño ya clasificado, con su marca temporal."""

    at: datetime
    added: float
    removed: float
    traded: float


class MicrostructureEngine:
    """Track per-symbol order-book dynamics from incremental updates.

    Args:
        settings: Ventana, mínimos y nocional de referencia del impacto.
    """

    def __init__(self, settings: QuantMicrostructureSettings) -> None:
        self._settings = settings
        self._events: dict[str, deque[_Event]] = {}
        self._levels: dict[str, dict[tuple[str, float], float]] = {}
        self._books: dict[str, OrderBook] = {}
        self._log = logging.getLogger("app.engine.microstructure")

    # ------------------------------------------------------------------
    # Ingesta (camino caliente: sólo aritmética sobre dicts)
    # ------------------------------------------------------------------

    def observe_delta(self, delta: OrderBookDelta, book: OrderBook | None = None) -> None:
        """Record one incremental book update.

        Un snapshot completo **reinicia** el estado de niveles en vez de
        contarse como altas masivas: si no, cada resync del libro se leería
        como una avalancha de órdenes nuevas y dispararía el ``arrival_rate``
        justo cuando lo que ha pasado es que se perdió la conexión.

        Args:
            delta: Actualización normalizada del libro.
            book: Libro reconstruido tras aplicarla, si está disponible.
        """
        if not self._settings.enabled:
            return
        symbol = delta.symbol
        levels = self._levels.setdefault(symbol, {})
        if delta.is_snapshot:
            levels.clear()
            for side, rows in (("bid", delta.bids), ("ask", delta.asks)):
                for level in rows:
                    if level.size > 0:
                        levels[(side, level.price)] = level.size
            if book is not None:
                self._books[symbol] = book
            return

        added = 0.0
        removed = 0.0
        for side, rows in (("bid", delta.bids), ("ask", delta.asks)):
            for level in rows:
                key = (side, level.price)
                previous = levels.get(key, 0.0)
                if level.size <= 0:
                    removed += previous
                    levels.pop(key, None)
                    continue
                change = level.size - previous
                if change >= 0:
                    added += change
                else:
                    removed += -change
                levels[key] = level.size
        self._push(symbol, _Event(at=delta.local_ts, added=added, removed=removed, traded=0.0))
        if book is not None:
            self._books[symbol] = book

    def observe_trade(self, trade: Trade) -> None:
        """Record one executed trade (liquidity actually consumed).

        Sin esto, una ejecución y una cancelación son el mismo hueco en el
        libro, y ``cancel_rate`` mediría las dos cosas a la vez — que es
        precisamente lo que hace inútil a esa métrica.

        Args:
            trade: Operación publicada por el proveedor.
        """
        if not self._settings.enabled:
            return
        self._push(
            trade.symbol,
            _Event(at=trade.exchange_ts, added=0.0, removed=0.0, traded=abs(trade.size)),
        )

    def _push(self, symbol: str, event: _Event) -> None:
        """Append an event and drop whatever fell out of the window."""
        events = self._events.setdefault(symbol, deque(maxlen=self._settings.max_events_per_symbol))
        events.append(event)

    # ------------------------------------------------------------------
    # Cálculo
    # ------------------------------------------------------------------

    def snapshot(self, symbol: str, now: datetime | None = None) -> MicrostructureSnapshot:
        """Compute the current microstructure state for a symbol.

        Args:
            symbol: Activo consultado.
            now: Instante de referencia (inyectable para tests).

        Returns:
            El snapshot. Con libro ausente o muestra insuficiente devuelve
            ``observable=False``, motivo y todas las métricas en ``None``.
        """
        moment = now or utc_now()
        if not self._settings.enabled:
            return MicrostructureSnapshot(
                symbol=symbol, at=moment, reason="microestructura deshabilitada"
            )
        book = self._books.get(symbol)
        if book is None:
            # El caso real de la demo: el proveedor no publica libro.
            return MicrostructureSnapshot(
                symbol=symbol,
                at=moment,
                reason="sin libro de órdenes del proveedor",
            )
        window = [
            e
            for e in self._events.get(symbol, ())
            if (moment - e.at).total_seconds() <= self._settings.window_seconds
        ]
        if len(window) < self._settings.min_updates:
            return MicrostructureSnapshot(
                symbol=symbol,
                at=moment,
                reason=f"muestra {len(window)} < mínimo {self._settings.min_updates}",
                updates=len(window),
            )

        elapsed = max(
            (window[-1].at - window[0].at).total_seconds(), self._settings.min_elapsed_seconds
        )
        added = sum(e.added for e in window)
        removed = sum(e.removed for e in window)
        traded = sum(e.traded for e in window)
        # Lo retirado que no fue ejecutado es cancelación. El máximo con cero
        # evita que un desfase de timestamps entre el feed de libro y el de
        # operaciones produzca una tasa de cancelación negativa.
        cancelled = max(0.0, removed - traded)

        imbalance = _imbalance(book, self._settings.depth_levels)
        arrival = added / elapsed
        cancels = cancelled / elapsed
        consumption = traded / elapsed
        replenishment = added / elapsed
        consumed_total = cancelled + traded
        resiliency = added / consumed_total if consumed_total > 0 else None
        impact = _impact_bps(book, self._settings.impact_notional)
        pressure = _pressure(imbalance, resiliency, self._settings.resiliency_reference)

        return MicrostructureSnapshot(
            symbol=symbol,
            at=moment,
            observable=True,
            queue_imbalance=imbalance,
            queue_ahead=_queue_ahead(book),
            order_arrival_rate=arrival,
            cancel_rate=cancels,
            book_resiliency=resiliency,
            replenishment=replenishment,
            liquidity_consumption=consumption,
            market_impact_bps=impact,
            execution_pressure=pressure,
            updates=len(window),
        )

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "symbols": sorted(self._books),
            "events": {symbol: len(events) for symbol, events in sorted(self._events.items())},
            "snapshots": {
                symbol: self.snapshot(symbol).to_dict() for symbol in sorted(self._books)
            },
        }


# ---------------------------------------------------------------------------
# Métricas puras sobre el libro
# ---------------------------------------------------------------------------


def _imbalance(book: OrderBook, levels: int) -> float | None:
    """Size imbalance across the top ``levels`` of the book, -1..1."""
    bids = sum(level.size for level in book.bids[:levels])
    asks = sum(level.size for level in book.asks[:levels])
    total = bids + asks
    if total <= 0:
        return None
    return (bids - asks) / total


def _queue_ahead(book: OrderBook) -> float | None:
    """Displayed size sitting at the best price (queue a new order joins)."""
    best_bid = book.best_bid
    best_ask = book.best_ask
    if best_bid is None or best_ask is None:
        return None
    return (best_bid.size + best_ask.size) / 2.0


def _impact_bps(book: OrderBook, notional: float) -> float | None:
    """Estimate the cost, in bps, of sweeping the book for a given notional.

    Camina el lado vendedor acumulando nocional hasta cubrirlo y compara el
    precio medio conseguido con el punto medio. Si el libro visible no da para
    el nocional pedido devuelve ``None``: extrapolar más allá del último nivel
    conocido sería inventarse profundidad que nadie ha publicado.
    """
    mid = book.mid
    if mid is None or notional <= 0 or not book.asks:
        return None
    remaining = notional
    cost = 0.0
    filled = 0.0
    for level in book.asks:
        available = level.price * level.size
        take = min(available, remaining)
        if take <= 0:
            continue
        quantity = take / level.price
        cost += quantity * level.price
        filled += quantity
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0 or filled <= 0:
        return None
    average = cost / filled
    return (average - mid) / mid * 10_000.0


def _pressure(imbalance: float | None, resiliency: float | None, reference: float) -> float | None:
    """Blend imbalance and resiliency into a 0..1 hostility score.

    1.0 = libro que no repone lo que se le come y con el peso del tamaño en
    contra. Los dos ingredientes se promedian sin ponderar: no hay evidencia
    todavía para afirmar que uno importe más que el otro, y fingir un peso
    calibrado sería darle a la cifra una autoridad que no tiene.
    """
    parts: list[float] = []
    if imbalance is not None:
        parts.append(abs(imbalance))
    if resiliency is not None and reference > 0:
        parts.append(max(0.0, min(1.0, 1.0 - resiliency / reference)))
    if not parts:
        return None
    return sum(parts) / len(parts)

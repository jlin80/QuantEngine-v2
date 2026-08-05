"""Microstructure Engine (Bloque 3): deltas, cancelaciones, impacto y ausencia."""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import QuantMicrostructureSettings
from app.engine.microstructure import MicrostructureEngine
from app.market.models.market import DepthLevel, OrderBook, OrderBookDelta, Trade

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _book(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBook:
    return OrderBook(
        symbol="BTCUSDT",
        provider="binance",
        bids=tuple(DepthLevel(price=p, size=s) for p, s in bids),
        asks=tuple(DepthLevel(price=p, size=s) for p, s in asks),
        exchange_ts=_START,
        local_ts=_START,
    )


def _delta(
    second: float,
    *,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
    snapshot: bool = False,
) -> OrderBookDelta:
    at = _START + timedelta(seconds=second)
    return OrderBookDelta(
        symbol="BTCUSDT",
        provider="binance",
        bids=tuple(DepthLevel(price=p, size=s) for p, s in (bids or ())),
        asks=tuple(DepthLevel(price=p, size=s) for p, s in (asks or ())),
        is_snapshot=snapshot,
        exchange_ts=at,
        local_ts=at,
    )


def _trade(second: float, size: float) -> Trade:
    at = _START + timedelta(seconds=second)
    return Trade(
        symbol="BTCUSDT",
        provider="binance",
        price=100.0,
        size=size,
        exchange_ts=at,
        local_ts=at,
    )


def _engine(**overrides) -> MicrostructureEngine:
    defaults = {"min_updates": 2, "depth_levels": 5}
    return MicrostructureEngine(QuantMicrostructureSettings(**{**defaults, **overrides}))


def _feed(engine: MicrostructureEngine, book: OrderBook | None = None) -> None:
    """Un flujo mínimo: snapshot inicial + altas + una bajada."""
    engine.observe_delta(_delta(0, bids=[(99.0, 10.0)], asks=[(101.0, 10.0)], snapshot=True), book)
    engine.observe_delta(_delta(1, bids=[(99.0, 14.0)]), book)
    engine.observe_delta(_delta(2, asks=[(101.0, 6.0)]), book)


# ---------------------------------------------------------------------------
# Ausencia de libro: el caso real de la demo
# ---------------------------------------------------------------------------


def test_without_an_order_book_nothing_is_observable() -> None:
    # MT5 no publica libro. Devolver 0.0 aquí sería indistinguible de un libro
    # medido y perfectamente equilibrado, y alimentaría al Decision Engine con
    # una lectura de mercado que nadie tomó.
    snapshot = _engine().snapshot("BTCUSDm")
    assert snapshot.observable is False
    assert "sin libro" in snapshot.reason
    assert snapshot.queue_imbalance is None
    assert snapshot.execution_pressure is None
    assert snapshot.market_impact_bps is None


def test_a_thin_sample_is_declared_not_guessed() -> None:
    engine = _engine(min_updates=50)
    _feed(engine, _book([(99.0, 10.0)], [(101.0, 10.0)]))
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3))
    assert snapshot.observable is False
    assert "mínimo 50" in snapshot.reason
    assert snapshot.updates == 2


def test_disabled_engine_reports_why_instead_of_silently_returning_zeros() -> None:
    snapshot = _engine(enabled=False).snapshot("BTCUSDT")
    assert snapshot.observable is False
    assert "deshabilitada" in snapshot.reason


# ---------------------------------------------------------------------------
# Métricas del libro
# ---------------------------------------------------------------------------


def test_queue_imbalance_reads_the_weight_of_each_side() -> None:
    engine = _engine()
    _feed(engine, _book([(99.0, 30.0)], [(101.0, 10.0)]))
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3))
    assert snapshot.observable is True
    assert snapshot.queue_imbalance == pytest.approx(0.5)


def test_queue_ahead_is_the_size_a_new_limit_order_queues_behind() -> None:
    engine = _engine()
    _feed(engine, _book([(99.0, 8.0)], [(101.0, 12.0)]))
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3))
    assert snapshot.queue_ahead == pytest.approx(10.0)


def test_a_deep_book_costs_less_to_sweep_than_a_thin_one() -> None:
    deep = _engine()
    _feed(deep, _book([(99.0, 100.0)], [(101.0, 100.0), (101.5, 100.0)]))
    thin = _engine()
    _feed(thin, _book([(99.0, 1.0)], [(101.0, 1.0), (150.0, 100.0)]))
    now = _START + timedelta(seconds=3)
    deep_impact = deep.snapshot("BTCUSDT", now=now).market_impact_bps
    thin_impact = thin.snapshot("BTCUSDT", now=now).market_impact_bps
    assert deep_impact is not None and thin_impact is not None
    assert thin_impact > deep_impact


def test_impact_is_none_when_the_book_cannot_cover_the_notional() -> None:
    # Extrapolar más allá del último nivel publicado sería inventarse
    # profundidad que nadie ha mostrado.
    engine = _engine(impact_notional=1_000_000.0)
    _feed(engine, _book([(99.0, 1.0)], [(101.0, 1.0)]))
    assert engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3)).market_impact_bps is None


# ---------------------------------------------------------------------------
# Dinámica: altas, cancelaciones y ejecuciones
# ---------------------------------------------------------------------------


def test_a_trade_is_consumption_not_cancellation() -> None:
    # Sin separar las dos cosas, `cancel_rate` mediría ejecuciones también, que
    # es justo lo que la haría inútil.
    engine = _engine()
    book = _book([(99.0, 10.0)], [(101.0, 10.0)])
    engine.observe_delta(_delta(0, bids=[(99.0, 10.0)], snapshot=True), book)
    engine.observe_delta(_delta(1, bids=[(99.0, 4.0)]), book)  # -6 de tamaño
    engine.observe_trade(_trade(1, 6.0))  # los 6 fueron ejecutados
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=2))
    assert snapshot.cancel_rate == pytest.approx(0.0)
    assert snapshot.liquidity_consumption is not None
    assert snapshot.liquidity_consumption > 0.0


def test_a_pulled_level_without_a_trade_counts_as_cancellation() -> None:
    engine = _engine()
    book = _book([(99.0, 10.0)], [(101.0, 10.0)])
    engine.observe_delta(_delta(0, bids=[(99.0, 10.0)], snapshot=True), book)
    engine.observe_delta(_delta(1, bids=[(99.0, 0.0)]), book)  # nivel retirado
    engine.observe_delta(_delta(2, bids=[(98.0, 5.0)]), book)
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3))
    assert snapshot.cancel_rate is not None and snapshot.cancel_rate > 0.0
    assert snapshot.liquidity_consumption == pytest.approx(0.0)


def test_a_resync_snapshot_is_not_read_as_a_flood_of_new_orders() -> None:
    # Si el snapshot contara como altas masivas, cada pérdida de conexión
    # dispararía el arrival rate justo cuando lo que pasó fue quedarse ciego.
    engine = _engine()
    book = _book([(99.0, 10.0)], [(101.0, 10.0)])
    engine.observe_delta(_delta(0, bids=[(99.0, 10.0)], snapshot=True), book)
    engine.observe_delta(_delta(1, bids=[(99.0, 11.0)]), book)
    engine.observe_delta(_delta(2, bids=[(99.0, 999.0)], snapshot=True), book)
    engine.observe_delta(_delta(3, bids=[(99.0, 1000.0)]), book)
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=4))
    assert snapshot.order_arrival_rate is not None
    assert snapshot.order_arrival_rate < 10.0


def test_resiliency_compares_what_is_replaced_with_what_is_eaten() -> None:
    engine = _engine()
    book = _book([(99.0, 10.0)], [(101.0, 10.0)])
    engine.observe_delta(_delta(0, bids=[(99.0, 10.0)], snapshot=True), book)
    engine.observe_delta(_delta(1, bids=[(99.0, 4.0)]), book)  # se comen 6
    engine.observe_delta(_delta(2, bids=[(99.0, 10.0)]), book)  # repone 6
    snapshot = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3))
    assert snapshot.book_resiliency == pytest.approx(1.0)


def test_events_outside_the_window_stop_counting() -> None:
    engine = _engine(window_seconds=5.0)
    book = _book([(99.0, 10.0)], [(101.0, 10.0)])
    _feed(engine, book)
    stale = engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=600))
    assert stale.observable is False
    assert stale.updates == 0


def test_execution_pressure_rises_when_the_book_stops_replacing_liquidity() -> None:
    calm = _engine()
    calm_book = _book([(99.0, 10.0)], [(101.0, 10.0)])
    calm.observe_delta(_delta(0, bids=[(99.0, 10.0)], snapshot=True), calm_book)
    calm.observe_delta(_delta(1, bids=[(99.0, 4.0)]), calm_book)
    calm.observe_delta(_delta(2, bids=[(99.0, 10.0)]), calm_book)

    hostile = _engine()
    hostile_book = _book([(99.0, 1.0)], [(101.0, 30.0)])
    hostile.observe_delta(_delta(0, bids=[(99.0, 10.0)], snapshot=True), hostile_book)
    hostile.observe_delta(_delta(1, bids=[(99.0, 4.0)]), hostile_book)
    hostile.observe_delta(_delta(2, bids=[(99.0, 1.0)]), hostile_book)

    now = _START + timedelta(seconds=3)
    calm_pressure = calm.snapshot("BTCUSDT", now=now).execution_pressure
    hostile_pressure = hostile.snapshot("BTCUSDT", now=now).execution_pressure
    assert calm_pressure is not None and hostile_pressure is not None
    assert hostile_pressure > calm_pressure


def test_symbols_are_tracked_independently() -> None:
    engine = _engine()
    _feed(engine, _book([(99.0, 10.0)], [(101.0, 10.0)]))
    assert engine.snapshot("BTCUSDT", now=_START + timedelta(seconds=3)).observable is True
    assert engine.snapshot("ETHUSDT").observable is False

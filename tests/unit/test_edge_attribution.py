"""Edge Attribution Engine (Bloque 2): bucketing, join, atribución y honestidad."""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import QuantAttributionSettings
from app.engine.attribution import (
    EdgeAttributionEngine,
    FactorSnapshot,
    FactorSnapshotStore,
)
from app.execution.models.enums import PositionSide
from app.execution.models.trades import TradeRecord

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _trade(index: int, r: float, *, decision_id: str | None = None) -> TradeRecord:
    """Operación cerrada sintética con una R conocida."""
    entry = _START + timedelta(minutes=index)
    return TradeRecord(
        trade_id=f"t-{index}",
        position_id=f"p-{index}",
        symbol="BTCUSDm",
        side=PositionSide.LONG,
        quantity=0.01,
        entry_time=entry,
        exit_time=entry + timedelta(minutes=10),
        entry_price=100.0,
        exit_price=100.0 + r,
        pnl=r * 10.0,
        r_multiple=r,
        strategy="alpha",
        decision_id=f"d-{index}" if decision_id is None else decision_id,
    )


def _snapshot(index: int, delta: float, *, regime: str = "trending") -> FactorSnapshot:
    """Foto de factores con un `delta` controlado y el resto constante."""
    return FactorSnapshot(
        decision_id=f"d-{index}",
        symbol="BTCUSDm",
        at=_START + timedelta(minutes=index),
        numeric={"delta": delta, "spread_bps": 1.0, "ml": None},
        labels={"strategy": "alpha", "regime": regime},
        accepted=True,
    )


def _engine(trades, snapshots, **overrides) -> EdgeAttributionEngine:
    """Motor sobre listas fijas de operaciones y fotos."""
    store = FactorSnapshotStore(None, persist=False)
    for snapshot in snapshots:
        store.record(snapshot)
    defaults = {"min_sample": 9, "min_bucket": 3}
    settings = QuantAttributionSettings(**{**defaults, **overrides})
    return EdgeAttributionEngine(settings, lambda: list(trades), store)


def _informative_sample(n: int = 30):
    """`delta` alto acompaña a ganadoras y `delta` bajo a perdedoras."""
    trades, snapshots = [], []
    for i in range(n):
        delta = float(i)
        r = 1.0 if i >= 2 * n // 3 else (-1.0 if i < n // 3 else 0.0)
        trades.append(_trade(i, r))
        snapshots.append(_snapshot(i, delta))
    return trades, snapshots


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------


def test_without_enough_matches_it_reports_why_not_a_fabricated_explanation() -> None:
    trades = [_trade(i, 1.0) for i in range(5)]
    report = _engine(trades, []).generate()
    assert report.factors == ()
    assert report.join_breakdown["without_snapshot"] == 5
    assert report.matched == 0


def test_the_join_breakdown_separates_the_two_reasons_for_missing() -> None:
    # "sin decision_id" (trade adoptado del broker) y "sin foto" (decisión
    # anterior al bloque) son problemas distintos: agregarlos escondería cuál
    # de los dos duele.
    trades, snapshots = _informative_sample()
    trades.append(_trade(99, 1.0, decision_id=""))
    trades.append(_trade(98, 1.0))
    report = _engine(trades, snapshots).generate()
    assert report.join_breakdown["without_decision_id"] == 1
    assert report.join_breakdown["without_snapshot"] == 1
    assert report.join_breakdown["matched"] == 30


# ---------------------------------------------------------------------------
# Agregado
# ---------------------------------------------------------------------------


def test_a_factor_that_discriminates_shows_a_positive_spread() -> None:
    report = _engine(*_informative_sample()).generate()
    delta = next(f for f in report.factors if f.factor == "delta")
    # Los terciles reparten 30 operaciones en 11/9/10: el bucket bajo arrastra
    # una operación neutra, así que su media es -0.909 y no -1.0. El número
    # exacto importa menos que el hecho de que el factor separa ganadoras de
    # perdedoras y de que la muestra de cada bucket viaja con él.
    assert delta.buckets["high"]["mean_r"] == pytest.approx(1.0)
    assert delta.buckets["low"]["mean_r"] < 0.0
    assert delta.spread_r > 1.5
    assert delta.buckets["high"]["lift_r"] > 0.0
    assert sum(b["sample"] for b in delta.buckets.values()) == 30


def test_a_constant_factor_is_dropped_instead_of_reported_as_neutral() -> None:
    # Un factor sin variación no discrimina nada; trocearlo produciría buckets
    # vacíos con lift 0 que ensucian el informe.
    report = _engine(*_informative_sample()).generate()
    assert all(f.factor != "spread_bps" for f in report.factors)


def test_an_unobserved_factor_never_enters_the_report() -> None:
    report = _engine(*_informative_sample()).generate()
    assert all(f.factor != "ml" for f in report.factors)


def test_tiny_buckets_are_discarded_so_noise_does_not_top_the_ranking() -> None:
    trades, snapshots = _informative_sample()
    # Dos operaciones en un régimen exótico: lift enorme, significado nulo.
    for i in (100, 101):
        trades.append(_trade(i, 5.0))
        snapshots.append(_snapshot(i, 1.0, regime="exotico"))
    report = _engine(trades, snapshots, min_bucket=5).generate()
    regime = next((f for f in report.factors if f.factor == "regime"), None)
    assert regime is None or "exotico" not in regime.buckets


def test_factors_are_ranked_by_how_much_they_discriminate() -> None:
    report = _engine(*_informative_sample()).generate()
    spreads = [abs(f.spread_r) for f in report.factors]
    assert spreads == sorted(spreads, reverse=True)


# ---------------------------------------------------------------------------
# Atribución por operación
# ---------------------------------------------------------------------------


def test_a_winner_is_explained_by_the_bucket_it_fell_into() -> None:
    trades, snapshots = _informative_sample()
    engine = _engine(trades, snapshots)
    engine.generate()
    found = engine.explain("t-29")
    assert found is not None
    delta = next(c for c in found.contributions if c.factor == "delta")
    assert delta.bucket == "high"
    assert delta.lift_r > 0.0


def test_the_residual_is_always_reported_not_hidden() -> None:
    # Un residuo grande significa que la explicación no explica. Ocultarlo
    # convertiría el informe en una narración que siempre suena convincente.
    trades, snapshots = _informative_sample()
    engine = _engine(trades, snapshots)
    engine.generate()
    found = engine.explain("t-29")
    assert found is not None
    assert found.residual_r == pytest.approx(
        found.r_multiple - found.baseline_r - found.explained_r
    )
    assert "residual_r" in found.to_dict()


def test_the_report_carries_its_own_caveat_into_the_dashboard() -> None:
    # Se va a leer fuera del contexto de la doc: el aviso viaja en la carga útil.
    report = _engine(*_informative_sample()).generate()
    assert "no causa" in report.to_dict()["caveat"]


def test_a_trade_without_snapshot_cannot_be_explained() -> None:
    trades, snapshots = _informative_sample()
    trades.append(_trade(97, 1.0))
    engine = _engine(trades, snapshots)
    engine.generate()
    assert engine.explain("t-97") is None
    assert engine.explain("no-existe") is None


def test_explain_recent_returns_only_explainable_trades_newest_first() -> None:
    trades, snapshots = _informative_sample()
    engine = _engine(trades, snapshots)
    engine.generate()
    found = engine.explain_recent(limit=3)
    assert [a.trade_id for a in found] == ["t-29", "t-28", "t-27"]


def test_nothing_can_be_explained_before_the_first_cycle() -> None:
    trades, snapshots = _informative_sample()
    engine = _engine(trades, snapshots)
    assert engine.explain("t-29") is None
    assert engine.explain_recent() == []


# ---------------------------------------------------------------------------
# Store de fotos
# ---------------------------------------------------------------------------


def test_snapshots_persist_and_reload(tmp_path) -> None:
    store = FactorSnapshotStore(tmp_path / "snap.jsonl", flush_size=2)
    store.record(_snapshot(0, 1.0))
    store.record(_snapshot(1, 2.0))
    reloaded = list(FactorSnapshotStore(tmp_path / "snap.jsonl").load())
    assert [s.decision_id for s in reloaded] == ["d-0", "d-1"]
    assert reloaded[0].numeric["delta"] == 1.0


def test_the_pending_batch_is_visible_to_the_join(tmp_path) -> None:
    # Sin esto, una operación abierta y cerrada dentro del mismo lote se
    # quedaría sin explicación por un detalle de buffering.
    store = FactorSnapshotStore(tmp_path / "snap.jsonl", flush_size=100)
    store.record(_snapshot(0, 1.0))
    assert "d-0" in store.index()


def test_a_corrupt_snapshot_line_does_not_hide_the_rest(tmp_path) -> None:
    path = tmp_path / "snap.jsonl"
    store = FactorSnapshotStore(path, flush_size=1)
    store.record(_snapshot(0, 1.0))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{roto\n")
    store.record(_snapshot(1, 2.0))
    assert len(list(store.load())) == 2


def test_unobserved_numeric_factors_survive_a_round_trip(tmp_path) -> None:
    # `None` es "no observable"; convertirlo en 0.0 al releer sería inventar
    # una observación que nunca existió.
    store = FactorSnapshotStore(tmp_path / "snap.jsonl", flush_size=1)
    store.record(_snapshot(0, 1.0))
    reloaded = next(iter(FactorSnapshotStore(tmp_path / "snap.jsonl").load()))
    assert reloaded.numeric["ml"] is None

"""Why Not Trade Engine (Bloque 14): desglose, agregación y persistencia."""

from datetime import UTC, datetime

import pytest
from app.config.settings import QuantRejectionsSettings
from app.engine.rejections import GateResult, RejectionRecord, RejectionStore

_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _record(decision_id: str, *blocked: str, symbol: str = "BTCUSDm") -> RejectionRecord:
    """Rechazo con las puertas indicadas bloqueando y el resto pasando."""
    names = ["min_score", "min_confidence", "spread", "microstructure"]
    return RejectionRecord(
        decision_id=decision_id,
        symbol=symbol,
        at=_AT,
        initial_score=72.0,
        confidence=0.61,
        direction="long",
        gates=tuple(
            GateResult(
                name=name,
                kind="threshold" if name.startswith("min_") else "filter",
                passed=name not in blocked,
                value=50.0 if name in blocked and name.startswith("min_") else None,
                required=60.0 if name in blocked and name.startswith("min_") else None,
                reason=f"{name} no superado" if name in blocked else "",
            )
            for name in names
        ),
        evidence={"context": {"regime": "ranging"}},
    )


def _store(**overrides) -> RejectionStore:
    defaults = {"persist": False}
    return RejectionStore(QuantRejectionsSettings(**{**defaults, **overrides}))


# ---------------------------------------------------------------------------
# El registro
# ---------------------------------------------------------------------------


def test_a_threshold_reports_how_much_was_missing() -> None:
    # Es la única "penalización" real que existe: un filtro no resta, veta.
    record = _record("d-1", "min_score")
    gate = next(g for g in record.gates if g.name == "min_score")
    assert gate.deficit == pytest.approx(10.0)


def test_a_filter_has_no_deficit_because_it_does_not_subtract() -> None:
    record = _record("d-1", "spread")
    gate = next(g for g in record.gates if g.name == "spread")
    assert gate.kind == "filter"
    assert gate.deficit is None


def test_a_passed_gate_never_reports_a_deficit() -> None:
    record = _record("d-1", "spread")
    assert all(g.deficit is None for g in record.gates if g.passed)


def test_a_blocked_opportunity_has_a_final_score_of_zero() -> None:
    # No se opera, así que el score efectivo de la oportunidad es cero.
    record = _record("d-1", "spread")
    assert record.initial_score == 72.0
    assert record.final_score == 0.0


def test_an_accepted_decision_keeps_its_score() -> None:
    record = _record("d-1")
    assert record.blocked_by == ()
    assert record.final_score == 72.0
    assert record.primary_reason == ""


def test_the_primary_reason_is_the_first_gate_that_failed() -> None:
    record = _record("d-1", "min_confidence", "spread")
    assert record.primary_reason == "min_confidence no superado"
    assert record.blocked_by == ("min_confidence", "spread")


# ---------------------------------------------------------------------------
# Agregación
# ---------------------------------------------------------------------------


def test_the_summary_counts_how_often_each_gate_blocks() -> None:
    store = _store()
    store.record(_record("d-1", "spread"))
    store.record(_record("d-2", "spread"))
    store.record(_record("d-3", "min_score"))
    summary = store.summary()
    assert summary["blocked_by"]["spread"] == 2
    assert summary["blocked_by"]["min_score"] == 1
    assert summary["sample"] == 3


def test_the_summary_separates_blocking_from_being_the_only_blocker() -> None:
    # Una puerta que siempre bloquea acompañada de otras no cuesta nada:
    # quitarla no habría dejado pasar ni una sola operación.
    store = _store()
    store.record(_record("d-1", "spread", "min_score"))
    store.record(_record("d-2", "spread", "min_score"))
    store.record(_record("d-3", "min_score"))
    summary = store.summary()
    assert summary["blocked_by"]["spread"] == 2
    assert "spread" not in summary["sole_blocker"]
    assert summary["sole_blocker"]["min_score"] == 1


def test_accepted_decisions_are_the_denominator() -> None:
    # "Este filtro bloqueó 40 veces" no significa nada sin saber sobre cuántas
    # oportunidades.
    store = _store()
    store.record(_record("d-1", "spread"))
    for index in range(9):
        store.record(_record(f"ok-{index}"))
    assert store.summary()["sample"] == 10
    assert store.summary()["blocked_by"]["spread"] == 1


def test_recent_filters_by_symbol_and_returns_newest_first() -> None:
    store = _store()
    store.record(_record("d-1", "spread", symbol="BTCUSDm"))
    store.record(_record("d-2", "spread", symbol="ETHUSDm"))
    store.record(_record("d-3", "spread", symbol="BTCUSDm"))
    found = store.recent(symbol="BTCUSDm")
    assert [r.decision_id for r in found] == ["d-3", "d-1"]


def test_a_disabled_store_records_nothing() -> None:
    store = _store(enabled=False)
    store.record(_record("d-1", "spread"))
    assert store.status()["recorded"] == 0


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def test_rejections_survive_a_round_trip(tmp_path) -> None:
    path = tmp_path / "rejections.jsonl"
    store = RejectionStore(QuantRejectionsSettings(persist=True, path=path, flush_size=1))
    store.record(_record("d-1", "min_score"))
    reloaded = list(RejectionStore(QuantRejectionsSettings(persist=True, path=path)).load())
    assert len(reloaded) == 1
    assert reloaded[0].primary_reason == "min_score no superado"
    assert reloaded[0].evidence["context"]["regime"] == "ranging"


def test_a_corrupt_line_does_not_hide_the_rest(tmp_path) -> None:
    path = tmp_path / "rejections.jsonl"
    store = RejectionStore(QuantRejectionsSettings(persist=True, path=path, flush_size=1))
    store.record(_record("d-1", "spread"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{roto\n")
    store.record(_record("d-2", "spread"))
    assert len(list(store.load())) == 2


def test_a_disk_failure_never_kills_the_engine(tmp_path, monkeypatch) -> None:
    store = RejectionStore(
        QuantRejectionsSettings(persist=True, path=tmp_path / "r.jsonl", flush_size=1)
    )

    def _boom(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr("pathlib.Path.open", _boom)
    store.record(_record("d-1", "spread"))
    assert store.status()["dropped"] == 1
    assert store.status()["recorded"] == 1

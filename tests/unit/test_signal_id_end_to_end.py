"""El `signal_id` viaja de la señal al dataset, y el join separa señal de ejecución.

El Bloque 4 dejó escrito que la calidad de la señal sólo se podía **aproximar**
desde el motivo de salida de la operación, porque el resultado virtual no se
persistía por señal y el `TradeRecord` no llevaba los `signal_id`. Aquí se fija
el contrato de los dos huecos ya cerrados:

1. la propagación completa señal → decisión → orden → posición → operación, y
2. el join fila a fila con el evaluador continuo, incluidos los tres casos sin
   match — que se cuentan, no se esconden.
"""

import json
from datetime import UTC, datetime, timedelta

from app.config.settings import MLDataQualitySettings, QuantEvaluationSettings
from app.engine.evaluation import (
    PerformanceTracker,
    VirtualOutcome,
    VirtualOutcomeStore,
    index_outcomes,
)
from app.engine.events import DecisionGenerated
from app.engine.models import Direction
from app.execution.models import ExitReason, Fill, OrderSide
from app.execution.position_manager import PositionManager
from app.ml.datasets import (
    MATCHED,
    SIGNAL_LABEL,
    UNMATCHED_LEGACY,
    UNMATCHED_UNRESOLVED,
    DatasetBuilder,
    SignalOutcome,
    join_trades_with_outcomes,
)
from app.production.recovery.snapshot import position_from_state, position_to_state

from tests.unit.execution_helpers import (
    make_engine,
    make_market_with_state,
)
from tests.unit.ml_helpers import make_trade
from tests.unit.quant_helpers import make_candles, make_ticker

_BASE = datetime(2026, 8, 1, tzinfo=UTC)


def _decision(*, signal_ids: tuple[str, ...]) -> DecisionGenerated:
    return DecisionGenerated(
        source="test",
        decision_id="d1",
        symbol="BTCUSDT",
        action="open_long",
        accepted=True,
        score=80.0,
        confidence=0.8,
        summary="momentum",
        strategy="bos",
        strategy_category="smc",
        signal_ids=signal_ids,
    )


def _outcome(signal_id: str, *, r: float, strategy: str = "bos") -> VirtualOutcome:
    return VirtualOutcome(
        signal_id=signal_id,
        strategy=strategy,
        symbol="BTCUSDT",
        direction=Direction.LONG.value,
        entry=100.0,
        stop=98.0,
        target=106.0,
        r_multiple=r,
        outcome="win" if r >= 0 else "loss",
        false_signal=False,
        opened_at=_BASE,
        closed_at=_BASE + timedelta(minutes=20),
    )


def _signal_outcome(signal_id: str, *, r: float, strategy: str = "bos") -> SignalOutcome:
    return SignalOutcome(
        signal_id=signal_id,
        strategy=strategy,
        r_multiple=r,
        outcome="win" if r >= 0 else "loss",
    )


# ----------------------------------------------------------------------
# Propagación extremo a extremo
# ----------------------------------------------------------------------


async def test_signal_ids_travel_from_the_decision_to_the_journalled_trade():
    """La traza completa: decisión → orden → posición → operación cerrada.

    Es el criterio de aceptación del bloque: cualquier operación del journal
    debe poder remontarse hasta las señales que la originaron.
    """
    market, state = make_market_with_state(
        candles=make_candles([100.0, 101.0, 100.5, 101.5, 102.0] * 6),
        ticker=make_ticker(bid=101.9, ask=101.95),
    )
    engine = make_engine(market)

    position = await engine.process_decision(_decision(signal_ids=("sig-a", "sig-b")))

    assert position is not None
    assert position.signal_ids == ("sig-a", "sig-b")
    # La orden que la abrió lleva la misma trazabilidad.
    assert engine.orders.recent()[0].request.signal_ids == ("sig-a", "sig-b")

    state.update_ticker(make_ticker(bid=106.5, ask=106.6))
    await engine.close_position(position, ExitReason.TAKE_PROFIT)

    trade = engine.journal.all()[-1]
    assert trade.position_id == position.position_id
    assert trade.signal_ids == ("sig-a", "sig-b")


def test_a_journal_line_round_trips_its_signal_ids():
    """El journal es JSONL: si no sobreviven a to_dict/from_dict, no sirven."""
    trade = make_trade(signal_ids=("sig-a", "sig-b"))

    revived = type(trade).from_dict(json.loads(json.dumps(trade.to_dict())))

    assert revived.signal_ids == ("sig-a", "sig-b")


def test_a_pre_block8_journal_line_still_loads():
    """El historial acumulado no lleva la clave y debe releerse sin migración."""
    payload = make_trade().to_dict()
    del payload["signal_ids"]

    revived = type(make_trade()).from_dict(payload)

    assert revived.signal_ids == ()


def test_a_restarted_position_keeps_its_signals_and_its_strategy():
    """El snapshot de recuperación no persistía ni la atribución ni las señales.

    Sin esto la traza se rompía en cada reinicio: la posición restaurada caía al
    holding global en vez del suyo por estrategia, y su operación llegaba al
    journal sin nada que unir con el evaluador.
    """
    manager = PositionManager()
    position = manager.open(
        Fill(
            request_id="req",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=1.0,
            requested_quantity=1.0,
            reference_price=100.0,
            price=100.0,
            executed_at=_BASE,
        ),
        stop_loss=98.0,
        take_profit=106.0,
        decision_id="d1",
        signal_ids=("sig-a",),
        strategy="bos",
        strategy_category="smc",
    )

    revived = position_from_state(json.loads(json.dumps(position_to_state(position))))

    assert revived.signal_ids == ("sig-a",)
    assert revived.strategy == "bos"
    assert revived.strategy_category == "smc"


# ----------------------------------------------------------------------
# Store del resultado virtual por señal
# ----------------------------------------------------------------------


def test_the_evaluator_now_keeps_the_row_not_just_the_aggregate(tmp_path):
    """Antes del bloque el resultado individual se descartaba en `_resolve`."""
    store = VirtualOutcomeStore(tmp_path / "outcomes.jsonl", flush_size=1)

    store.record(_outcome("sig-a", r=2.0))
    store.record(_outcome("sig-b", r=-1.0))

    index = store.index()
    assert set(index) == {"sig-a", "sig-b"}
    assert index["sig-a"].r_multiple == 2.0
    assert index["sig-b"].outcome == "loss"


def test_the_store_batches_and_flushes_on_stop(tmp_path):
    """Un `open()` por señal resuelta sería I/O gratuito en el evaluador."""
    path = tmp_path / "outcomes.jsonl"
    store = VirtualOutcomeStore(path, flush_size=10)

    store.record(_outcome("sig-a", r=1.0))
    assert not path.exists()  # todavía en el lote

    assert store.flush() == 1
    assert len(list(store.load())) == 1


def test_one_corrupt_line_never_blocks_the_rest(tmp_path):
    """Mismo criterio que el Trade Journal: una línea rota no tumba el fichero."""
    path = tmp_path / "outcomes.jsonl"
    store = VirtualOutcomeStore(path, flush_size=1)
    store.record(_outcome("sig-a", r=1.0))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{no es json\n")
    store.record(_outcome("sig-b", r=1.0))

    assert set(store.index()) == {"sig-a", "sig-b"}


def test_a_tracker_without_a_store_behaves_exactly_as_before():
    """El store es opcional: sin él el evaluador sigue siendo el de la Fase 4."""
    market, _ = make_market_with_state(candles=make_candles([100.0] * 10))

    tracker = PerformanceTracker(QuantEvaluationSettings(), market)

    assert tracker.status()["outcomes"] is None


# ----------------------------------------------------------------------
# El join, y sus tres casos sin match
# ----------------------------------------------------------------------


def test_the_join_matches_a_trade_with_the_outcome_of_its_signal():
    trades = [make_trade(signal_ids=("sig-a",))]

    result = join_trades_with_outcomes(trades, [_signal_outcome("sig-a", r=2.0)])

    row = result.rows[0]
    assert row.status == MATCHED
    assert row.signal_r == 2.0
    assert row.signal_had_edge is True


def test_a_trade_with_several_signals_averages_them():
    """La decisión es multi-estrategia: quedarse con una sola sería atribuir mal."""
    trades = [make_trade(signal_ids=("sig-a", "sig-b"))]

    result = join_trades_with_outcomes(
        trades,
        [_signal_outcome("sig-a", r=3.0), _signal_outcome("sig-b", r=-1.0)],
    )

    assert result.rows[0].signal_r == 1.0


def test_a_trade_without_signal_ids_is_legacy_not_a_failure():
    """El journal anterior al bloque es la mayor parte del historial."""
    result = join_trades_with_outcomes([make_trade()], [])

    assert result.rows[0].status == UNMATCHED_LEGACY
    assert result.rows[0].signal_had_edge is None


def test_a_trade_whose_signal_is_still_open_is_unresolved_not_legacy():
    """Distinguirlos importa: uno es historial viejo, el otro una señal en curso."""
    result = join_trades_with_outcomes([make_trade(signal_ids=("sig-z",))], [])

    assert result.rows[0].status == UNMATCHED_UNRESOLVED


def test_a_signal_that_never_traded_is_counted_apart():
    """Filtrada o vetada por riesgo: no es fila del dataset, pero es evidencia."""
    result = join_trades_with_outcomes(
        [make_trade(signal_ids=("sig-a",))],
        [_signal_outcome("sig-a", r=1.0), _signal_outcome("sig-huerfana", r=2.5)],
    )

    assert len(result.signal_without_trade) == 1
    assert result.signal_without_trade[0].signal_id == "sig-huerfana"


def test_the_join_breakdown_is_never_empty():
    """Mismo estándar que el `era_breakdown` del Bloque 4: procedencia auditable."""
    result = join_trades_with_outcomes(
        [make_trade(signal_ids=("sig-a",)), make_trade()],
        [_signal_outcome("sig-a", r=1.0)],
    )

    breakdown = result.breakdown()
    assert breakdown["trades"] == 2
    assert breakdown[MATCHED] == 1
    assert breakdown[UNMATCHED_LEGACY] == 1


# ----------------------------------------------------------------------
# El dataset del ML: lo que el join cambia de verdad
# ----------------------------------------------------------------------


def test_the_signal_label_now_comes_from_the_evaluator_not_the_exit_reason():
    """El caso que la aproximación **no podía** juzgar, y era el que importaba.

    Una operación que la ejecución cortó por régimen nunca puso a prueba su
    tesis, así que el Bloque 4 tenía que descartarla. El evaluador continuo sí
    la resolvió, contra precio posterior a la señal: aquí se comprueba que esa
    operación entra al dataset y que su etiqueta la pone la señal, no el motivo
    de salida — que es exactamente la distinción "la señal tenía edge" vs "la
    ejecución lo capturó".
    """
    trade = make_trade(
        signal_ids=("sig-a",),
        exit_reason=ExitReason.REGIME_CHANGE,
        r_multiple=-0.4,  # la ejecución perdió...
    )
    outcomes = {"sig-a": _signal_outcome("sig-a", r=2.5)}  # ...pero la señal acertó

    dataset = DatasetBuilder(data_quality=MLDataQualitySettings()).build(
        [trade], label=SIGNAL_LABEL, outcomes=outcomes
    )

    assert dataset.metadata["samples"] == 1
    assert dataset.y == [1]
    assert dataset.metadata["labelled_from_join"] == 1


def test_without_the_join_that_same_trade_is_still_discarded():
    """Contraste explícito: sin resultado virtual, la aproximación no la juzga."""
    trade = make_trade(
        signal_ids=("sig-a",),
        exit_reason=ExitReason.REGIME_CHANGE,
        r_multiple=-0.4,
    )

    dataset = DatasetBuilder(data_quality=MLDataQualitySettings()).build(
        [trade], label=SIGNAL_LABEL
    )

    assert dataset.metadata["samples"] == 0
    assert dataset.metadata["dropped_not_signal_verdict"] == 1


def test_legacy_trades_still_build_a_dataset_through_the_old_path():
    """El fallback no es opcional: hoy es la mayor parte del historial."""
    trades = [make_trade(exit_reason=ExitReason.TAKE_PROFIT, r_multiple=1.5)]

    dataset = DatasetBuilder(data_quality=MLDataQualitySettings()).build(
        trades, label=SIGNAL_LABEL, outcomes={}
    )

    assert dataset.metadata["samples"] == 1
    assert dataset.metadata["labelled_from_join"] == 0
    assert dataset.metadata["join_breakdown"][UNMATCHED_LEGACY] == 1


def test_the_era_sanitation_of_block_4_survives_the_join():
    """El join cambia cómo se etiqueta, no qué operaciones son medibles."""
    dirty = make_trade(
        signal_ids=("sig-a",),
        entry_time=datetime(2026, 7, 20, tzinfo=UTC),
        exit_time=datetime(2026, 7, 20, 1, tzinfo=UTC),
    )

    dataset = DatasetBuilder(data_quality=MLDataQualitySettings()).build(
        [dirty], label=SIGNAL_LABEL, outcomes={"sig-a": _signal_outcome("sig-a", r=2.0)}
    )

    assert dataset.metadata["samples"] == 0
    assert dataset.metadata["dropped_by_era"] == 1


def test_index_outcomes_keeps_the_first_resolution():
    """El store es append-only: la primera resolución es la que valió."""
    index = index_outcomes([_outcome("sig-a", r=1.0), _outcome("sig-a", r=9.0)])

    assert index["sig-a"].r_multiple == 1.0

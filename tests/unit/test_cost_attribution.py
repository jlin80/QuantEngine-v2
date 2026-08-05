"""Cost Attribution Engine (Bloque 9): reparto, residuo y oportunidad."""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import CostAttributionSettings
from app.engine.evaluation.outcomes import VirtualOutcome
from app.execution.costs import CostAttributionEngine
from app.execution.models.enums import PositionSide
from app.execution.models.trades import TradeRecord

_START = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _trade(
    index: int,
    *,
    net: float = 10.0,
    commission: float = 1.0,
    slippage_bps: float = 0.0,
    spread_bps: float = 0.0,
    contract_size: float = 1.0,
    day: int = 1,
    signal_ids: tuple[str, ...] = (),
    extra_cost: float = 0.0,
) -> TradeRecord:
    """Operación cuyo bruto cuadra exactamente con los costes declarados.

    ``extra_cost`` simula un coste real que el sistema **no** registra: es lo
    que debe aparecer como residuo.
    """
    entry = _START.replace(day=day) + timedelta(minutes=index)
    notional = 100.0 * 0.01 * contract_size
    measured = commission + notional * (slippage_bps + spread_bps) / 10_000.0
    return TradeRecord(
        trade_id=f"t-{index}",
        position_id=f"p-{index}",
        symbol="BTCUSDm",
        side=PositionSide.LONG,
        quantity=0.01,
        contract_size=contract_size,
        entry_time=entry,
        exit_time=entry + timedelta(minutes=5),
        entry_price=100.0,
        exit_price=101.0,
        pnl=net,
        pnl_gross=net + measured + extra_cost,
        commission=commission,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        r_multiple=net / 10.0,
        strategy="alpha",
        signal_ids=signal_ids,
    )


def _outcome(signal_id: str, r: float) -> VirtualOutcome:
    return VirtualOutcome(
        signal_id=signal_id,
        strategy="alpha",
        symbol="BTCUSDm",
        direction="long",
        entry=100.0,
        stop=99.0,
        target=102.0,
        r_multiple=r,
        outcome="win" if r > 0 else "loss",
        false_signal=False,
        opened_at=_START,
        closed_at=_START + timedelta(minutes=5),
    )


def _engine(trades, outcomes=None, **overrides) -> CostAttributionEngine:
    settings = CostAttributionSettings(**overrides)
    return CostAttributionEngine(
        settings,
        lambda: list(trades),
        None if outcomes is None else (lambda: list(outcomes)),
    )


def test_the_measured_costs_are_split_one_by_one() -> None:
    trades = [_trade(i, commission=2.0, slippage_bps=10.0, spread_bps=5.0) for i in range(4)]
    total = _engine(trades).analyze().total
    assert total.commission == pytest.approx(8.0)
    assert total.slippage > 0.0
    assert total.spread > 0.0
    assert total.total_measured == pytest.approx(total.commission + total.slippage + total.spread)


def test_a_fully_explained_trade_leaves_no_residual() -> None:
    trades = [_trade(i, commission=1.0, slippage_bps=8.0, spread_bps=4.0) for i in range(5)]
    assert _engine(trades).analyze().total.hidden == pytest.approx(0.0, abs=1e-9)


def test_an_unmeasured_cost_shows_up_as_residual_and_raises_a_note() -> None:
    # El residuo es un detector de contabilidad incompleta: si crece, falta
    # instrumentar algo.
    trades = [_trade(i, net=10.0, extra_cost=5.0) for i in range(5)]
    report = _engine(trades).analyze()
    assert report.total.hidden == pytest.approx(25.0)
    assert any("no está midiendo" in note for note in report.notes)


def test_latency_is_reported_as_unmeasured_not_as_zero() -> None:
    # Estimarla la mezclaría con el residuo y perderíamos justo esa señal.
    report = _engine([_trade(0)]).analyze()
    assert report.total.latency is None
    assert any("latencia no medido" in note for note in report.notes)


def test_bps_costs_are_converted_over_units_not_lots() -> None:
    # En oro un lote son 100 onzas: medir sobre `quantity` dejaría el coste 100
    # veces por debajo — el mismo error del incidente del contract_size.
    one = _engine([_trade(0, slippage_bps=10.0, contract_size=1.0)]).analyze().total.slippage
    gold = _engine([_trade(0, slippage_bps=10.0, contract_size=100.0)]).analyze().total.slippage
    assert gold == pytest.approx(one * 100.0)


def test_the_daily_report_groups_by_exit_day() -> None:
    trades = [_trade(i, day=1) for i in range(3)] + [_trade(10 + i, day=2) for i in range(2)]
    daily = _engine(trades).analyze().daily
    assert set(daily) == {"2026-08-01", "2026-08-02"}
    assert daily["2026-08-01"].trades == 3
    assert daily["2026-08-02"].trades == 2


def test_opportunity_cost_counts_only_the_signals_that_would_have_won() -> None:
    # Una señal no ejecutada que habría perdido no es coste de oportunidad, es
    # una bala esquivada; sumarla con signo contrario dejaría el número en nada.
    outcomes = [_outcome("s-win", 2.0), _outcome("s-loss", -1.0), _outcome("s-taken", 3.0)]
    trades = [_trade(0, signal_ids=("s-taken",))]
    assert _engine(trades, outcomes).analyze().total.opportunity == pytest.approx(2.0)


def test_opportunity_cost_is_unmeasured_without_virtual_outcomes() -> None:
    report = _engine([_trade(0)]).analyze()
    assert report.total.opportunity is None
    assert any("oportunidad no medido" in note for note in report.notes)


def test_the_daily_breakdown_does_not_repeat_the_opportunity_cost() -> None:
    # Es un coste del conjunto, no de un día: repartirlo por día lo contaría
    # tantas veces como días tenga el informe.
    report = _engine([_trade(0)], [_outcome("s-win", 2.0)]).analyze()
    assert report.total.opportunity == pytest.approx(2.0)
    assert all(day.opportunity is None for day in report.daily.values())


def test_no_trades_produces_an_empty_report_not_a_crash() -> None:
    report = _engine([]).analyze()
    assert report.total.trades == 0
    assert report.daily == {}

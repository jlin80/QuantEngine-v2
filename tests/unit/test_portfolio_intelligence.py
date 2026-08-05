"""Portfolio Intelligence (Bloque 8): desglose, concentracion y avisos."""

from datetime import UTC, datetime, timedelta

import pytest
from app.config.settings import PortfolioIntelligenceSettings
from app.execution.models.enums import PositionSide
from app.execution.models.trades import TradeRecord
from app.portfolio import DIMENSIONS, PortfolioIntelligence

_START = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _trade(
    index: int,
    pnl: float,
    *,
    symbol: str = "BTCUSDm",
    strategy: str = "alpha",
    regime: str = "trending",
    hour: int = 10,
    commission: float = 0.5,
) -> TradeRecord:
    entry = _START.replace(hour=hour) + timedelta(minutes=index)
    return TradeRecord(
        trade_id=f"t-{index}",
        position_id=f"p-{index}",
        symbol=symbol,
        side=PositionSide.LONG,
        quantity=0.01,
        entry_time=entry,
        exit_time=entry + timedelta(minutes=5),
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        pnl_gross=pnl + commission,
        commission=commission,
        r_multiple=pnl / 10.0,
        strategy=strategy,
        regime=regime,
    )


def _intelligence(trades, **overrides) -> PortfolioIntelligence:
    defaults = {"min_sample": 5}
    settings = PortfolioIntelligenceSettings(**{**defaults, **overrides})
    return PortfolioIntelligence(settings, lambda: list(trades))


def test_every_requested_dimension_is_broken_down() -> None:
    report = _intelligence([_trade(i, 10.0) for i in range(10)]).analyze()
    assert set(report.contributions) == set(DIMENSIONS)


def test_the_pnl_source_is_split_between_gross_and_costs() -> None:
    report = _intelligence([_trade(i, 10.0, commission=2.0) for i in range(10)]).analyze()
    assert report.net_pnl == pytest.approx(100.0)
    assert report.gross_pnl == pytest.approx(120.0)
    assert report.commission == pytest.approx(20.0)


def test_contributions_carry_their_sample_so_a_streak_is_visible() -> None:
    # "El 80% del PnL vino de vol_breakout" se lee como merito cuando puede ser
    # una muestra de nueve operaciones.
    trades = [_trade(i, 1.0, strategy="steady") for i in range(20)]
    trades += [_trade(100 + i, 40.0, strategy="lucky") for i in range(2)]
    report = _intelligence(trades).analyze()
    by_strategy = {c.key: c for c in report.contributions["strategy"]}
    assert by_strategy["lucky"].pnl_share > by_strategy["steady"].pnl_share
    assert by_strategy["lucky"].trades == 2
    assert by_strategy["steady"].trades == 20


def test_shares_are_measured_against_what_was_won_not_the_net() -> None:
    # Con ganancias y perdidas mezcladas, la suma neta puede acercarse a cero y
    # las cuotas se disparan o cambian de signo.
    trades = [_trade(i, 10.0) for i in range(5)] + [_trade(50 + i, -9.9) for i in range(5)]
    report = _intelligence(trades).analyze()
    for contribution in report.contributions["symbol"]:
        assert -1.5 < contribution.pnl_share < 1.5


def test_unattributed_trades_get_their_own_group() -> None:
    # Son las adoptadas del broker y las anteriores a la trazabilidad: verlas
    # juntas dice cuanta parte del PnL no se puede explicar todavia.
    trades = [_trade(i, 5.0, strategy="") for i in range(6)]
    report = _intelligence(trades).analyze()
    assert report.contributions["strategy"][0].key == "unattributed"


def test_sessions_are_derived_from_the_entry_time() -> None:
    trades = [_trade(i, 5.0, hour=2) for i in range(3)]
    trades += [_trade(10 + i, 5.0, hour=15) for i in range(3)]
    report = _intelligence(trades).analyze()
    keys = {c.key for c in report.contributions["session"]}
    assert keys == {"asia", "america"}


def test_the_heatmap_shows_whether_a_strategy_lives_off_one_symbol() -> None:
    trades = [_trade(i, 5.0, symbol="BTCUSDm", strategy="alpha") for i in range(5)]
    trades += [_trade(10 + i, 5.0, symbol="ETHUSDm", strategy="beta") for i in range(5)]
    heatmap = _intelligence(trades).analyze().heatmap
    assert heatmap["BTCUSDm"] == {"alpha": 25.0}
    assert heatmap["ETHUSDm"] == {"beta": 25.0}


def test_concentration_is_maximal_when_one_source_produces_everything() -> None:
    report = _intelligence([_trade(i, 10.0, symbol="BTCUSDm") for i in range(10)]).analyze()
    assert report.concentration == pytest.approx(1.0)
    assert report.effective_bets == pytest.approx(1.0)


def test_spreading_the_pnl_lowers_concentration() -> None:
    trades = [_trade(i, 10.0, symbol="BTCUSDm") for i in range(5)]
    trades += [_trade(10 + i, 10.0, symbol="ETHUSDm") for i in range(5)]
    report = _intelligence(trades).analyze()
    assert report.concentration == pytest.approx(0.5)
    assert report.effective_bets == pytest.approx(2.0)


def test_a_losing_group_does_not_add_concentration() -> None:
    # Un grupo que pierde no concentra el origen del beneficio: lo diluye.
    trades = [_trade(i, 10.0, symbol="BTCUSDm") for i in range(5)]
    trades += [_trade(10 + i, -10.0, symbol="ETHUSDm") for i in range(5)]
    report = _intelligence(trades).analyze()
    assert report.concentration == pytest.approx(1.0)


def test_a_thin_sample_is_flagged_but_still_computed() -> None:
    # Los numeros existen y esconderlos no ayuda; lo que hace falta es que nadie
    # los tome por concluyentes.
    report = _intelligence([_trade(0, 5.0)], min_sample=50).analyze()
    assert report.trades == 1
    assert "no son concluyentes" in report.sample_warning


def test_no_trades_produces_an_empty_report_not_a_crash() -> None:
    report = _intelligence([]).analyze()
    assert report.trades == 0
    assert report.concentration is None
    assert "sin operaciones" in report.sample_warning


def test_the_caveat_travels_into_the_dashboard() -> None:
    report = _intelligence([_trade(i, 5.0) for i in range(6)]).analyze()
    assert "no exposición viva" in report.to_dict()["caveat"]

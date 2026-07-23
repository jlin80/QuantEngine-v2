"""Risk Manager: límites, filtros y cortacircuitos."""

from app.config.settings import ExecutionRiskSettings
from app.execution.risk_manager import RiskManager, RiskQuery


def _query(**overrides: object) -> RiskQuery:
    data: dict[str, object] = {
        "symbol": "BTCUSDT",
        "new_notional": 100.0,
        "equity": 10_000.0,
        "open_positions": 0,
        "positions_on_symbol": 0,
        "symbol_exposures": {},
    }
    data.update(overrides)
    return RiskQuery(**data)  # type: ignore[arg-type]


def test_allows_a_clean_entry():
    rm = RiskManager(ExecutionRiskSettings(), 10_000.0)
    assert rm.evaluate_entry(_query()).allowed


def test_blocks_on_max_open_positions():
    rm = RiskManager(ExecutionRiskSettings(max_open_positions=2), 10_000.0)
    check = rm.evaluate_entry(_query(open_positions=2))
    assert not check.allowed and check.rule == "max_open_positions"


def test_blocks_on_total_exposure():
    rm = RiskManager(ExecutionRiskSettings(max_exposure_pct=50.0), 10_000.0)
    check = rm.evaluate_entry(_query(new_notional=1000.0, symbol_exposures={"ETHUSD": 4_600.0}))
    assert not check.allowed and check.rule == "max_exposure"


def test_blocks_on_symbol_exposure():
    rm = RiskManager(ExecutionRiskSettings(max_symbol_exposure_pct=10.0), 10_000.0)
    check = rm.evaluate_entry(_query(new_notional=600.0, symbol_exposures={"BTCUSDT": 500.0}))
    assert not check.allowed and check.rule == "max_symbol_exposure"


def test_blocks_on_correlation_group():
    settings = ExecutionRiskSettings(
        max_correlation_exposure_pct=20.0, correlation_groups=[["BTCUSDT", "ETHUSD"]]
    )
    rm = RiskManager(settings, 10_000.0)
    check = rm.evaluate_entry(_query(new_notional=500.0, symbol_exposures={"ETHUSD": 1_800.0}))
    assert not check.allowed and check.rule == "max_correlation_exposure"


def test_consecutive_losses_block_then_reset():
    rm = RiskManager(ExecutionRiskSettings(max_consecutive_losses=2), 10_000.0)
    rm.on_trade_closed(-10.0)
    rm.on_trade_closed(-10.0)
    assert not rm.evaluate_entry(_query()).allowed
    rm.on_trade_closed(5.0)  # una ganancia reinicia la racha
    assert rm.evaluate_entry(_query()).allowed


def test_daily_loss_limit_blocks():
    rm = RiskManager(ExecutionRiskSettings(max_daily_loss_pct=1.0), 10_000.0)
    rm.on_trade_closed(-150.0)  # >1% de 10.000
    check = rm.evaluate_entry(_query())
    assert not check.allowed and check.rule == "max_daily_loss"


def test_kill_switch_on_drawdown():
    rm = RiskManager(ExecutionRiskSettings(kill_switch_drawdown_pct=10.0), 10_000.0)
    rm.update_equity(12.0)
    assert rm.kill_switch_active
    assert not rm.evaluate_entry(_query()).allowed
    rm.reset_kill_switch()
    assert rm.evaluate_entry(_query()).allowed


def test_circuit_breaker_on_fast_loss():
    settings = ExecutionRiskSettings(
        circuit_breaker_loss_pct=2.0, circuit_breaker_window_minutes=15.0
    )
    rm = RiskManager(settings, 10_000.0)
    rm.on_trade_closed(-250.0)  # >2% en la ventana
    assert rm.circuit_breaker_active
    assert rm.evaluate_entry(_query()).rule == "circuit_breaker"


def test_spread_and_liquidity_filters():
    rm = RiskManager(ExecutionRiskSettings(max_spread_bps=5.0, min_liquidity=100.0), 10_000.0)
    assert not rm.check_spread(9.0).allowed
    assert rm.check_spread(3.0).allowed
    assert not rm.check_liquidity(50.0).allowed
    assert rm.check_liquidity(200.0).allowed

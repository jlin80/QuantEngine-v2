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


def test_correlation_check_applies_even_without_a_group():
    """El propio notional de la operación cuenta, tenga o no grupo asignado.

    ``correlated = _correlated_exposure() + new_notional``: aunque el símbolo
    no esté en ningún `correlation_groups`, su propio notional sigue sumando
    contra el tope. No es un caso raro — es el camino normal para un símbolo
    sin grupo configurado (``correlation_groups=[]`` por defecto).
    """
    rm = RiskManager(ExecutionRiskSettings(max_correlation_exposure_pct=5.0), 10_000.0)
    check = rm.evaluate_entry(_query(new_notional=600.0))
    assert not check.allowed and check.rule == "max_correlation_exposure"


# --------------------------------------------------------------------------
# Overrides por símbolo (mismo problema que el sizing: XAUUSD contract_size=100
# hace que su notional mínimo no sea comparable al de BTC/ETH/USTEC)
# --------------------------------------------------------------------------


def test_symbol_exposure_override_lets_gold_through_without_loosening_others():
    settings = ExecutionRiskSettings(
        max_exposure_pct=2000.0,
        max_symbol_exposure_pct=40.0,
        max_symbol_exposure_pct_by_symbol={"XAUUSDM": 1100.0},
        max_correlation_exposure_pct=2000.0,
    )
    rm = RiskManager(settings, 440.75)
    gold = rm.evaluate_entry(_query(symbol="XAUUSDM", new_notional=4_378.0, equity=440.75))
    assert gold.allowed

    other = rm.evaluate_entry(_query(symbol="ETHUSDM", new_notional=4_378.0, equity=440.75))
    assert not other.allowed and other.rule == "max_symbol_exposure"


def test_correlation_exposure_override_lets_gold_through_without_loosening_others():
    settings = ExecutionRiskSettings(
        # Alto para ambos, así el símbolo de control pasa el chequeo de
        # exposición y llega al de correlación, que es el que este test mide.
        max_exposure_pct=2000.0,
        max_symbol_exposure_pct=2000.0,
        max_correlation_exposure_pct=60.0,
        max_correlation_exposure_pct_by_symbol={"XAUUSDM": 1100.0},
    )
    rm = RiskManager(settings, 440.75)
    gold = rm.evaluate_entry(_query(symbol="XAUUSDM", new_notional=4_378.0, equity=440.75))
    assert gold.allowed

    other = rm.evaluate_entry(_query(symbol="ETHUSDM", new_notional=4_378.0, equity=440.75))
    assert not other.allowed and other.rule == "max_correlation_exposure"


def test_exposure_resolvers_fall_back_to_global_for_unlisted_symbols():
    settings = ExecutionRiskSettings(
        max_symbol_exposure_pct=40.0,
        max_correlation_exposure_pct=60.0,
        max_symbol_exposure_pct_by_symbol={"XAUUSDM": 1100.0},
        max_correlation_exposure_pct_by_symbol={"XAUUSDM": 1100.0},
    )
    assert settings.max_symbol_exposure_pct_for("XAUUSDM") == 1100.0
    assert settings.max_symbol_exposure_pct_for("ETHUSDM") == 40.0
    assert settings.max_correlation_exposure_pct_for("xauusdm") == 1100.0  # normaliza mayúsculas
    assert settings.max_correlation_exposure_pct_for("BTCUSDM") == 60.0


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


def test_ignore_drawdown_limits_never_trips_kill_switch():
    settings = ExecutionRiskSettings(kill_switch_drawdown_pct=10.0, ignore_drawdown_limits=True)
    rm = RiskManager(settings, 10_000.0)
    rm.update_equity(80.0)
    assert not rm.kill_switch_active
    assert rm.evaluate_entry(_query()).allowed
    # El drawdown se sigue midiendo: el override no ciega la métrica.
    assert rm.status()["drawdown_pct"] == 80.0
    assert rm.status()["ignore_drawdown_limits"] is True


def test_ignore_drawdown_limits_releases_a_drawdown_kill_switch():
    settings = ExecutionRiskSettings(kill_switch_drawdown_pct=10.0)
    rm = RiskManager(settings, 10_000.0)
    rm.update_equity(12.0)
    assert rm.kill_switch_active
    settings.ignore_drawdown_limits = True  # el toggle del dashboard, en caliente
    rm.update_equity(15.0)
    assert not rm.kill_switch_active
    assert rm.evaluate_entry(_query()).allowed


def test_ignore_drawdown_limits_does_not_release_a_manual_kill_switch():
    settings = ExecutionRiskSettings(ignore_drawdown_limits=True)
    rm = RiskManager(settings, 10_000.0)
    rm.engage_kill_switch("parada manual del operador")
    rm.update_equity(50.0)
    assert rm.kill_switch_active
    assert not rm.evaluate_entry(_query()).allowed


def test_ignore_drawdown_limits_keeps_other_limits():
    settings = ExecutionRiskSettings(max_daily_loss_pct=1.0, ignore_drawdown_limits=True)
    rm = RiskManager(settings, 10_000.0)
    rm.on_trade_closed(-150.0)
    rm.update_equity(40.0)
    check = rm.evaluate_entry(_query())
    assert not check.allowed and check.rule == "max_daily_loss"


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


def test_max_positions_per_symbol_override():
    settings = ExecutionRiskSettings(
        max_positions_per_symbol=1,
        max_positions_per_symbol_by_symbol={"XAUUSDM": 5},
    )
    rm = RiskManager(settings, 10_000.0)
    gold = rm.evaluate_entry(_query(symbol="XAUUSDM", positions_on_symbol=3))
    assert gold.allowed

    other = rm.evaluate_entry(_query(symbol="ETHUSDM", positions_on_symbol=1))
    assert not other.allowed and other.rule == "max_positions_per_symbol"


def test_max_positions_per_symbol_resolver_falls_back_to_global():
    settings = ExecutionRiskSettings(
        max_positions_per_symbol=1, max_positions_per_symbol_by_symbol={"XAUUSDM": 5}
    )
    assert settings.max_positions_per_symbol_for("XAUUSDM") == 5
    assert settings.max_positions_per_symbol_for("ETHUSDM") == 1

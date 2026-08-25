"""Un limite en 0 desactiva el freno, no lo pone al maximo.

Hasta el 2026-08-21 el Risk Manager calculaba `limit = -abs(balance * 0/100)`,
o sea 0, y comparaba `realized <= limit`. Con un solo cero de configuracion el
freno quedaba disparado permanentemente: incluso sin una pérdida, `0 <= 0`.
Quien pusiera 0 para QUITAR el freno conseguia exactamente lo contrario.

Documentado en la bitacora del 2026-08-04 y sin corregir hasta hoy.
"""

from app.config.settings import ExecutionRiskSettings
from app.execution.risk_manager import RiskManager, RiskQuery


def _query() -> RiskQuery:
    """Entrada modesta: lo que se prueba son los frenos, no la exposicion."""
    return RiskQuery(
        symbol="XAUUSDM",
        new_notional=10.0,
        equity=1000.0,
        open_positions=0,
        positions_on_symbol=0,
        symbol_exposures={},
    )


def _manager(**overrides: object) -> RiskManager:
    # Los tres periodos y el cortacircuitos se apagan por defecto en el helper:
    # cada test enciende solo el que quiere probar, para que un fallo senale al
    # freno correcto y no al de al lado.
    base: dict[str, object] = {
        "max_consecutive_losses": 0,
        "max_daily_loss_pct": 0.0,
        "max_weekly_loss_pct": 0.0,
        "max_monthly_loss_pct": 0.0,
        "circuit_breaker_loss_pct": 0.0,
    }
    base.update(overrides)
    return RiskManager(ExecutionRiskSettings(**base), 1000.0)  # type: ignore[arg-type]


def test_a_daily_limit_of_zero_does_not_block_a_flat_account():
    manager = _manager()
    manager.on_trade_closed(-1.0)
    assert manager.evaluate_entry(_query()).allowed


def test_a_daily_limit_of_zero_does_not_block_after_losses():
    """El caso que mordia: perdidas reales con el freno supuestamente apagado."""
    manager = _manager()
    for _ in range(50):
        manager.on_trade_closed(-5.0)
    assert manager.evaluate_entry(_query()).allowed


def test_a_positive_daily_limit_still_blocks():
    """La correccion no debe desactivar el freno cuando SI esta configurado."""
    manager = _manager(max_daily_loss_pct=3.0)
    for _ in range(10):
        manager.on_trade_closed(-5.0)  # -50 sobre 1000 = 5% > 3%
    assert not manager.evaluate_entry(_query()).allowed


def test_a_circuit_breaker_of_zero_does_not_trip():
    manager = _manager()
    for _ in range(20):
        manager.on_trade_closed(-10.0)
    assert manager.evaluate_entry(_query()).allowed


def test_a_max_spread_of_zero_disables_the_filter():
    manager = _manager(max_spread_bps=0.0)
    assert manager.check_spread(50.0).allowed


def test_a_positive_max_spread_still_filters():
    manager = _manager(max_spread_bps=5.0)
    assert not manager.check_spread(50.0).allowed
    assert manager.check_spread(1.0).allowed


def test_the_drawdown_alarm_fires_even_with_the_brake_off():
    """Desactivar el freno no debe desactivar el aviso.

    La cuenta demo llego al 52.5 % de drawdown el 2026-08-25 y nada lo dijo:
    `update_equity` salia en silencio con `ignore_drawdown_limits`. Es la misma
    leccion del incidente del reloj — el motor estuvo 4 dias sin operar y nada
    aviso — aplicada al riesgo.
    """
    manager = _manager(ignore_drawdown_limits=True, kill_switch_drawdown_pct=20.0)

    manager.update_equity(10.0)
    assert not manager.drawdown_alarm

    manager.update_equity(52.5)
    assert manager.drawdown_alarm
    # Y sigue sin frenar: es un aviso, no un freno.
    assert not manager.kill_switch_active
    assert manager.evaluate_entry(_query()).allowed


def test_the_drawdown_alarm_rearms_after_recovering():
    manager = _manager(ignore_drawdown_limits=True, kill_switch_drawdown_pct=20.0)
    manager.update_equity(30.0)
    assert manager.drawdown_alarm

    manager.update_equity(5.0)
    assert not manager.drawdown_alarm

"""El maximo historico de equity sobrevive a los reinicios.

Hasta el 2026-08-28, `sync_from_broker(set_baseline=True)` hacia
`peak_equity = balance` en cada arranque. La cuenta demo llego al 52.5 % de
drawdown el 25/08, la maquina reinicio, y el motor volvio marcando 0 %. Al
mismo tiempo `graduation_gap.py` —que reconstruye el drawdown desde el journal—
veia 67.4 %.

El drawdown es la base del kill switch. Un freno cuya memoria se borra al
reiniciar no frena nada, y el motor reinicia en cada despliegue.
"""

from pathlib import Path

from app.execution.portfolio_manager import PortfolioManager


def test_the_peak_survives_a_restart(tmp_path: Path):
    peak_file = tmp_path / "equity_peak.json"

    primero = PortfolioManager(500.0, peak_path=peak_file)
    primero.sync_from_broker(500.0, set_baseline=True)
    primero.sync_from_broker(800.0)
    assert primero.peak_equity == 800.0

    # Reinicio: el broker reporta una cuenta ya caida.
    segundo = PortfolioManager(300.0, peak_path=peak_file)
    segundo.sync_from_broker(300.0, set_baseline=True)

    assert segundo.peak_equity == 800.0


def test_a_deposit_does_not_erase_the_previous_peak(tmp_path: Path):
    """Recargar la cuenta no debe hacer desaparecer un drawdown de la vista."""
    peak_file = tmp_path / "equity_peak.json"

    manager = PortfolioManager(500.0, peak_path=peak_file)
    manager.sync_from_broker(500.0, set_baseline=True)
    manager.sync_from_broker(200.0)  # perdidas
    manager.sync_from_broker(450.0)  # deposito

    assert manager.peak_equity == 500.0


def test_without_a_path_nothing_is_persisted(tmp_path: Path):
    """Backtests y tests no deben compartir estado entre corridas."""
    manager = PortfolioManager(500.0)
    manager.sync_from_broker(900.0)
    assert manager.peak_equity == 900.0
    assert not list(tmp_path.iterdir())


def test_an_unreadable_file_does_not_break_startup(tmp_path: Path):
    peak_file = tmp_path / "equity_peak.json"
    peak_file.write_text("esto no es json", encoding="utf-8")

    manager = PortfolioManager(400.0, peak_path=peak_file)

    assert manager.peak_equity == 400.0


def test_the_recovered_state_also_respects_the_stored_peak(tmp_path: Path):
    peak_file = tmp_path / "equity_peak.json"
    PortfolioManager(500.0, peak_path=peak_file).sync_from_broker(900.0)

    manager = PortfolioManager(300.0, peak_path=peak_file)
    manager.restore(
        balance=300.0,
        peak_equity=310.0,
        total_trades=5,
        wins=2,
        losses=3,
        commission_paid=0.0,
    )

    assert manager.peak_equity == 900.0

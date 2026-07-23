"""Motor de backtest, datasets, reloj de replay, métricas y ventanas (Fase 6)."""

from datetime import UTC, datetime

from app.backtesting.clock import ReplayClock
from app.backtesting.datasets import DatasetManager
from app.backtesting.decisions import MovingAverageCrossSource, open_decision
from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import StatisticsEngine
from app.backtesting.models import BacktestConfig, EquityPoint
from app.backtesting.monte_carlo import MonteCarloSimulator
from app.backtesting.walk_forward import generate_windows
from app.config.settings import BacktestingSettings, ExecutionSettings, MonteCarloSettings
from app.engine.events import DecisionGenerated
from app.execution.models import ExitReason, PositionSide, TradeRecord


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbol="BTCUSDT", timeframe="1m", initial_balance=10_000.0, spread_bps=2.0
    )


def _trade(pnl: float, r_multiple: float = 0.0) -> TradeRecord:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return TradeRecord(
        position_id="p",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=1.0,
        entry_time=start,
        exit_time=start,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        r_multiple=r_multiple,
        exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
    )


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------


def test_synthetic_dataset_is_deterministic_and_valid():
    a = DatasetManager().synthetic("BTCUSDT", "1m", count=50, seed=1)
    b = DatasetManager().synthetic("BTCUSDT", "1m", count=50, seed=1)
    assert len(a) == 50
    assert [c.close for c in a] == [c.close for c in b]
    for candle in a:
        assert candle.high >= max(candle.open, candle.close)
        assert candle.low <= min(candle.open, candle.close)
        assert candle.closed is True


def test_dataset_load_csv_dedupes_and_sorts(tmp_path):
    path = tmp_path / "ohlcv.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T00:01:00+00:00,2,3,1,2.5,10\n"
        "2024-01-01T00:00:00+00:00,1,2,0.5,1.5,10\n"
        "2024-01-01T00:01:00+00:00,2,4,1,3.5,20\n",  # duplicado: gana el último
        encoding="utf-8",
    )
    candles = DatasetManager().load(path, "BTCUSDT", "1m")
    assert len(candles) == 2
    assert candles[0].start < candles[1].start
    assert candles[1].close == 3.5  # el duplicado posterior sobrescribe


# --------------------------------------------------------------------------
# Reloj de replay
# --------------------------------------------------------------------------


def test_replay_clock_installs_and_restores():
    from app.utils.time import utc_now

    clock = ReplayClock(datetime(2024, 1, 1, tzinfo=UTC))
    with clock.installed():
        assert utc_now() == datetime(2024, 1, 1, tzinfo=UTC)
        clock.set(datetime(2024, 1, 2, tzinfo=UTC))
        assert utc_now().day == 2
    # Fuera del bloque vuelve el reloj de pared (año actual, no 2024).
    assert utc_now().year >= 2025


# --------------------------------------------------------------------------
# Motor de backtest
# --------------------------------------------------------------------------


def test_backtest_engine_runs_and_produces_curve():
    candles = DatasetManager().synthetic("BTCUSDT", "1m", count=400, volatility=0.004, seed=3)
    engine = BacktestEngine(BacktestingSettings(), ExecutionSettings(enabled=True))
    result = engine.run(candles, MovingAverageCrossSource(fast=5, slow=20), _config())
    assert result.bars == 400
    assert len(result.equity_curve) == 400
    assert "sharpe" in result.statistics
    assert "sqn" in result.statistics
    # Con spread y comisiones, alguna operación cierra por stop/trailing.
    assert result.statistics["total_trades"] >= 1


def test_backtest_engine_rejects_empty_series():
    engine = BacktestEngine(BacktestingSettings(), ExecutionSettings(enabled=True))
    try:
        engine.run([], MovingAverageCrossSource(), _config())
    except ValueError:
        return
    raise AssertionError("Debió rechazar una serie vacía")


def test_backtest_always_long_source_opens_a_position():
    candles = DatasetManager().synthetic("BTCUSDT", "1m", count=60, seed=5)

    class _AlwaysLong:
        def reset(self) -> None:
            pass

        def decide(self, symbol: str, series: object, index: int) -> DecisionGenerated | None:
            return open_decision(symbol, "open_long") if index == 25 else None

    engine = BacktestEngine(BacktestingSettings(), ExecutionSettings(enabled=True))
    result = engine.run(candles, _AlwaysLong(), _config())
    assert result.statistics["total_trades"] >= 1


# --------------------------------------------------------------------------
# Métricas extendidas
# --------------------------------------------------------------------------


def test_statistics_consecutive_and_sqn():
    trades = [_trade(10, 1.0), _trade(-5, -1.0), _trade(-4, -1.0), _trade(8, 1.0), _trade(6, 1.0)]
    stats = StatisticsEngine(10_000.0).compute(trades)
    assert stats.max_consecutive_losses == 2
    assert stats.max_consecutive_wins == 2
    assert stats.base.total_trades == 5
    assert stats.sqn != 0.0


def test_statistics_empty_is_all_zero():
    stats = StatisticsEngine(10_000.0).compute([])
    assert stats.sqn == 0.0
    assert stats.base.total_trades == 0


def test_statistics_exposure_from_equity_curve():
    now = datetime(2024, 1, 1, tzinfo=UTC)
    curve = [
        EquityPoint(now, 10_000.0, 10_000.0, 0.0, 0),
        EquityPoint(now, 10_050.0, 10_000.0, 0.0, 1),
        EquityPoint(now, 10_100.0, 10_100.0, 0.0, 0),
    ]
    stats = StatisticsEngine(10_000.0).compute([_trade(100, 1.0)], curve)
    assert 0.0 < stats.exposure_pct <= 100.0


# --------------------------------------------------------------------------
# Ventanas de walk-forward
# --------------------------------------------------------------------------


def test_rolling_windows_slide_fixed_train():
    windows = generate_windows(
        1000, scheme="rolling", train_size=400, validation_size=100, step=100
    )
    assert windows[0].train_start == 0
    assert windows[0].train_end == 400
    assert windows[0].test_end == 500
    assert windows[1].train_start == 100  # la ventana se desliza
    assert all(w.train_size == 400 for w in windows)


def test_expanding_windows_anchor_at_zero():
    windows = generate_windows(
        1000, scheme="expanding", train_size=400, validation_size=100, step=100
    )
    assert all(w.train_start == 0 for w in windows)
    assert windows[1].train_end > windows[0].train_end


def test_anchored_is_expanding_alias():
    a = generate_windows(600, scheme="anchored", train_size=300, validation_size=100, step=100)
    b = generate_windows(600, scheme="expanding", train_size=300, validation_size=100, step=100)
    assert [w.to_dict() for w in a] == [w.to_dict() for w in b]


# --------------------------------------------------------------------------
# Monte Carlo
# --------------------------------------------------------------------------


def test_monte_carlo_basic_properties():
    settings = MonteCarloSettings(simulations=300, initial_capital=10_000.0, random_seed=1)
    result = MonteCarloSimulator(settings).run([50, -30, 40, -20, 60, -10])
    assert result.simulations == 300
    assert 0.0 <= result.risk_of_ruin <= 1.0
    assert 0.0 <= result.profitable_probability <= 1.0
    assert result.worst_max_drawdown_pct >= result.expected_max_drawdown_pct


def test_monte_carlo_empty_is_zero():
    result = MonteCarloSimulator(MonteCarloSettings()).run([])
    assert result.simulations == 0
    assert result.risk_of_ruin == 0.0

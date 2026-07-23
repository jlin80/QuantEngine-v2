"""Helpers compartidos por las pruebas del Quant Research Lab (Fase 10)."""

import dataclasses
from collections.abc import Sequence

from app.backtesting.datasets import DatasetManager
from app.config.settings import CandidatePipelineSettings, QualificationCriteriaSettings
from app.market.models import Candle
from app.research.models import Combine, ContextFilter, SignalBlock, StrategyGenome, new_id


def candles(
    n: int = 400,
    *,
    drift: float = 0.0006,
    vol: float = 0.008,
    seed: int = 7,
    flow: float = 0.55,
    symbol: str = "BTCUSDT",
) -> list[Candle]:
    """Deterministic synthetic candles with injected order flow."""
    series = DatasetManager().synthetic(
        symbol, "1m", count=n, drift=drift, volatility=vol, seed=seed
    )
    return [
        dataclasses.replace(c, buy_volume=c.volume * flow, sell_volume=c.volume * (1.0 - flow))
        for c in series
    ]


def trend_genome(
    symbol: str = "BTCUSDT", *, fast: float = 5.0, slow: float = 20.0
) -> StrategyGenome:
    """A single-block EMA-cross genome (deterministic id via name hash)."""
    return StrategyGenome(
        id=new_id("g"),
        name=f"ema_{int(fast)}_{int(slow)}",
        symbol=symbol,
        timeframe="1m",
        blocks=(SignalBlock(kind="ema_cross", params={"fast": fast, "slow": slow}),),
        combine=Combine.ALL,
        allow_short=True,
    )


def filtered_genome(symbol: str = "BTCUSDT") -> StrategyGenome:
    """A two-block genome with a context filter (for compiler/space tests)."""
    return StrategyGenome(
        id=new_id("g"),
        name="ema+momentum",
        symbol=symbol,
        timeframe="1m",
        blocks=(
            SignalBlock(kind="ema_cross", params={"fast": 5.0, "slow": 20.0}),
            SignalBlock(kind="momentum", params={"period": 10.0, "threshold": 0.001}),
        ),
        filters=(
            ContextFilter(
                kind="volatility", params={"period": 14.0, "min_pct": 0.0, "max_pct": 1.0}
            ),
        ),
        combine=Combine.ANY,
        allow_short=True,
    )


def permissive_criteria() -> QualificationCriteriaSettings:
    """Criteria that any trading strategy passes (tests the plumbing, not edge)."""
    return QualificationCriteriaSettings(
        min_trades=1,
        min_profit_factor=0.0,
        min_sharpe=-100.0,
        max_drawdown_pct=100.0,
        min_expectancy=-100.0,
        min_sqn=-100.0,
        min_win_rate=0.0,
        require_walk_forward=False,
        require_monte_carlo=False,
        require_beat_benchmark=False,
        monte_carlo_max_drawdown_pct=100.0,
        min_robust_scenarios_pct=0.0,
    )


def fast_pipeline() -> CandidatePipelineSettings:
    """Pipeline settings without the heavy walk-forward stage (fast tests)."""
    return CandidatePipelineSettings(
        require_walk_forward=False,
        require_monte_carlo=True,
        require_benchmark=False,
        require_ml_review=False,
        require_risk_review=True,
    )


def pnls(values: Sequence[float]) -> list[float]:
    """Convenience for Monte Carlo/shadow inputs."""
    return list(values)

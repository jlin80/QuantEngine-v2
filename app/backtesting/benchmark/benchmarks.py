"""Motor de benchmarks del laboratorio (Fase 6).

Estrategias de referencia contra las que se compara cualquier estrategia
candidata: Buy & Hold, Random, EMA Cross y VWAP básico. Cada benchmark produce
un retorno close-to-close sobre las mismas velas (sin costes) para dar un suelo
honesto: si la estrategia no bate a estas referencias simples, no aporta edge.
"""

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.market.models import Candle


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Resultado de un benchmark sobre una serie de velas."""

    name: str
    return_pct: float
    sharpe: float
    exposure_pct: float
    bars: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "name": self.name,
            "return_pct": round(self.return_pct, 4),
            "sharpe": round(self.sharpe, 4),
            "exposure_pct": round(self.exposure_pct, 4),
            "bars": self.bars,
        }


class Benchmark(ABC):
    """Base class: a benchmark is a rule mapping candles to per-bar positions."""

    name: str = "benchmark"

    @abstractmethod
    def positions(self, candles: Sequence[Candle]) -> list[int]:
        """Return the position (+1 long, -1 short, 0 flat) held on each bar."""

    def evaluate(self, candles: Sequence[Candle]) -> BenchmarkResult:
        """Evaluate the benchmark close-to-close over the candles.

        Args:
            candles: Serie de velas cerradas.

        Returns:
            Retorno total, Sharpe por barra y exposición del benchmark.
        """
        if len(candles) < 2:
            return BenchmarkResult(self.name, 0.0, 0.0, 0.0, len(candles))
        positions = self.positions(candles)
        equity = 1.0
        bar_returns: list[float] = []
        active = 0
        for i in range(1, len(candles)):
            prev_close = candles[i - 1].close
            if prev_close <= 0:
                continue
            change = (candles[i].close - prev_close) / prev_close
            held = positions[i - 1]
            bar_return = held * change
            equity *= 1.0 + bar_return
            bar_returns.append(bar_return)
            active += int(held != 0)
        return_pct = (equity - 1.0) * 100.0
        return BenchmarkResult(
            name=self.name,
            return_pct=return_pct,
            sharpe=_sharpe(bar_returns),
            exposure_pct=active / len(bar_returns) * 100.0 if bar_returns else 0.0,
            bars=len(candles),
        )


def _sharpe(returns: Sequence[float]) -> float:
    """Per-bar Sharpe ratio of a return series (0 if undefined)."""
    if len(returns) < 2:
        return 0.0
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std <= 0:
        return 0.0
    return mean / std * math.sqrt(len(returns))


class BuyAndHold(Benchmark):
    """Always long."""

    name = "buy_and_hold"

    def positions(self, candles: Sequence[Candle]) -> list[int]:
        """Hold a long position on every bar."""
        return [1] * len(candles)


class RandomStrategy(Benchmark):
    """Random long/flat positions (deterministic via seed).

    Args:
        seed: Semilla del RNG.
        long_probability: Probabilidad de estar largo en cada barra.
    """

    name = "random"

    def __init__(self, *, seed: int = 7, long_probability: float = 0.5) -> None:
        self._seed = seed
        self._long_probability = long_probability

    def positions(self, candles: Sequence[Candle]) -> list[int]:
        """Randomly be long or flat on each bar."""
        rng = random.Random(self._seed)
        return [1 if rng.random() < self._long_probability else 0 for _ in candles]


class EMACross(Benchmark):
    """Long while the fast EMA is above the slow EMA, else flat.

    Args:
        fast: Periodo de la EMA rápida.
        slow: Periodo de la EMA lenta.
    """

    name = "ema_cross"

    def __init__(self, *, fast: int = 12, slow: int = 26) -> None:
        if fast >= slow:
            raise ValueError("fast debe ser menor que slow")
        self._fast = fast
        self._slow = slow

    def positions(self, candles: Sequence[Candle]) -> list[int]:
        """Long when the fast EMA exceeds the slow EMA."""
        closes = [c.close for c in candles]
        fast = _ema(closes, self._fast)
        slow = _ema(closes, self._slow)
        return [1 if f > s else 0 for f, s in zip(fast, slow, strict=True)]


class VWAPBasic(Benchmark):
    """Long while price is above the cumulative VWAP, else flat."""

    name = "vwap_basic"

    def positions(self, candles: Sequence[Candle]) -> list[int]:
        """Long when the close is above the running VWAP."""
        cum_pv = 0.0
        cum_vol = 0.0
        result: list[int] = []
        for candle in candles:
            volume = candle.volume if candle.volume > 0 else 1.0
            cum_pv += candle.close * volume
            cum_vol += volume
            vwap = cum_pv / cum_vol if cum_vol > 0 else candle.close
            result.append(1 if candle.close > vwap else 0)
        return result


def _ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average series (seeded with the first value)."""
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    ema = values[0]
    out = [ema]
    for value in values[1:]:
        ema = alpha * value + (1.0 - alpha) * ema
        out.append(ema)
    return out


class BenchmarkEngine:
    """Run a suite of benchmarks and compare a strategy against them.

    Args:
        benchmarks: Benchmarks a evaluar (por defecto los cuatro estándar).
    """

    def __init__(self, benchmarks: Sequence[Benchmark] | None = None) -> None:
        self._benchmarks: list[Benchmark] = (
            list(benchmarks)
            if benchmarks
            else [
                BuyAndHold(),
                RandomStrategy(),
                EMACross(),
                VWAPBasic(),
            ]
        )

    @property
    def names(self) -> list[str]:
        """Names of the configured benchmarks."""
        return [bench.name for bench in self._benchmarks]

    def evaluate(self, candles: Sequence[Candle]) -> dict[str, BenchmarkResult]:
        """Evaluate every benchmark over the candles."""
        return {bench.name: bench.evaluate(candles) for bench in self._benchmarks}

    def beaten_by(self, strategy_return_pct: float, candles: Sequence[Candle]) -> dict[str, bool]:
        """Return, per benchmark, whether the strategy return beats it.

        Args:
            strategy_return_pct: Retorno total de la estrategia candidata.
            candles: Serie de velas (para evaluar los benchmarks).

        Returns:
            Mapa benchmark → ``True`` si la estrategia lo supera.
        """
        results = self.evaluate(candles)
        return {name: strategy_return_pct > result.return_pct for name, result in results.items()}

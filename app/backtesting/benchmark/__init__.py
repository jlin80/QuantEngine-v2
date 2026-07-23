"""Motor de benchmarks del laboratorio (Fase 6)."""

from app.backtesting.benchmark.benchmarks import (
    Benchmark,
    BenchmarkEngine,
    BenchmarkResult,
    BuyAndHold,
    EMACross,
    RandomStrategy,
    VWAPBasic,
)

__all__ = [
    "Benchmark",
    "BenchmarkEngine",
    "BenchmarkResult",
    "BuyAndHold",
    "EMACross",
    "RandomStrategy",
    "VWAPBasic",
]

"""Ejecución concurrente de simulaciones (Fase 10)."""

import asyncio
from collections.abc import Callable, Sequence
from typing import TypeVar

from app.config.settings import SimulationClusterSettings

T = TypeVar("T")


class SimulationCluster:
    """Bounded-concurrency runner for independent simulation jobs.

    Args:
        settings: Configuración del clúster (workers y tamaño de lote).
    """

    def __init__(self, settings: SimulationClusterSettings) -> None:
        self._settings = settings

    @property
    def max_workers(self) -> int:
        """Configured worker ceiling."""
        return self._settings.max_workers

    async def run(self, jobs: Sequence[Callable[[], T]]) -> list[T]:
        """Run each job in a worker thread, bounded by ``max_workers``.

        Args:
            jobs: Trabajos sin argumentos (p. ej. backtests ya cerrados sobre un
                genoma). Deben ser independientes entre sí.

        Returns:
            Los resultados en el mismo orden que ``jobs``.
        """
        if not jobs:
            return []
        semaphore = asyncio.Semaphore(max(1, self._settings.max_workers))

        async def _one(job: Callable[[], T]) -> T:
            async with semaphore:
                return await asyncio.to_thread(job)

        return await asyncio.gather(*(_one(job) for job in jobs))

    def run_sync(self, jobs: Sequence[Callable[[], T]]) -> list[T]:
        """Sequential fallback (no event loop required)."""
        return [job() for job in jobs]

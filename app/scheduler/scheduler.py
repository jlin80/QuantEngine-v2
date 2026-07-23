"""Scheduler asíncrono para tareas periódicas.

Usos futuros: limpieza de cache, métricas, sincronización con Notion,
respaldos, actualización de modelos. Cada job corre en su propia task; el
error de un job se registra y cuenta pero nunca detiene el scheduler ni a
los demás jobs.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.core.exceptions import SchedulerError
from app.core.lifecycle import Service
from app.utils.time import utc_now

JobCallable = Callable[[], Awaitable[None]]
"""Corrutina sin argumentos ejecutada en cada disparo del job."""


@dataclass(slots=True)
class _Job:
    """Definición interna + estado de ejecución de un job."""

    name: str
    func: JobCallable
    interval_seconds: float
    jitter_seconds: float = 0.0
    run_immediately: bool = False
    run_count: int = 0
    error_count: int = 0
    last_run_at: datetime | None = None
    last_error: str = ""
    task: asyncio.Task[None] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class JobInfo:
    """Vista de solo lectura del estado de un job."""

    name: str
    interval_seconds: float
    run_count: int
    error_count: int
    last_run_at: datetime | None
    last_error: str


class AsyncScheduler(Service):
    """Asyncio-based periodic task scheduler."""

    def __init__(self) -> None:
        super().__init__("scheduler")
        self._jobs: dict[str, _Job] = {}
        self._log = logging.getLogger("app.scheduler")

    def add_job(
        self,
        name: str,
        func: JobCallable,
        *,
        interval_seconds: float,
        jitter_seconds: float = 0.0,
        run_immediately: bool = False,
    ) -> None:
        """Register a periodic job.

        Args:
            name: Unique job name.
            func: Zero-argument coroutine executed on each tick.
            interval_seconds: Time between executions.
            jitter_seconds: Random extra delay to de-synchronize jobs.
            run_immediately: Execute once right after start.

        Raises:
            SchedulerError: If the name is duplicated or interval invalid.
        """
        if name in self._jobs:
            raise SchedulerError(f"Duplicate job name '{name}'", context={"job": name})
        if interval_seconds <= 0:
            raise SchedulerError(
                "Job interval must be positive",
                context={"job": name, "interval": interval_seconds},
            )
        job = _Job(
            name=name,
            func=func,
            interval_seconds=interval_seconds,
            jitter_seconds=max(0.0, jitter_seconds),
            run_immediately=run_immediately,
        )
        self._jobs[name] = job
        if self.is_running:
            job.task = asyncio.create_task(self._run_job(job), name=f"job-{name}")

    def remove_job(self, name: str) -> None:
        """Deregister a job and cancel its task if running."""
        job = self._jobs.pop(name, None)
        if job is not None and job.task is not None:
            job.task.cancel()

    @property
    def jobs(self) -> list[JobInfo]:
        """Read-only snapshot of every registered job."""
        return [
            JobInfo(
                name=j.name,
                interval_seconds=j.interval_seconds,
                run_count=j.run_count,
                error_count=j.error_count,
                last_run_at=j.last_run_at,
                last_error=j.last_error,
            )
            for j in self._jobs.values()
        ]

    async def _run_job(self, job: _Job) -> None:
        """Loop of a single job: sleep → run → repeat, with error isolation."""
        if job.run_immediately:
            await self._execute(job)
        while True:
            delay = job.interval_seconds
            if job.jitter_seconds:
                delay += random.uniform(0.0, job.jitter_seconds)
            await asyncio.sleep(delay)
            await self._execute(job)

    async def _execute(self, job: _Job) -> None:
        """Run one tick of a job, recording outcome."""
        try:
            await job.func()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.error_count += 1
            job.last_error = repr(exc)
            self._log.exception("Job '%s' failed (errors=%d)", job.name, job.error_count)
        else:
            job.run_count += 1
        finally:
            job.last_run_at = utc_now()

    async def _on_start(self) -> None:
        """Launch one task per registered job."""
        for job in self._jobs.values():
            if job.task is None or job.task.done():
                job.task = asyncio.create_task(self._run_job(job), name=f"job-{job.name}")

    async def _on_stop(self) -> None:
        """Cancel every job task."""
        for job in self._jobs.values():
            if job.task is not None:
                job.task.cancel()
        for job in self._jobs.values():
            if job.task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await job.task
                job.task = None

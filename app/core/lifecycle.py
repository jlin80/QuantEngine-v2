"""Ciclo de vida uniforme para todos los servicios del sistema.

Cada módulo de larga vida (event bus, scheduler, health monitor, watchdog,
API, notificaciones...) implementa :class:`Service`. El motor y el watchdog
solo conocen esta interfaz — nunca los detalles internos de cada módulo.
"""

import abc
import enum
import logging
from typing import final

from app.core.exceptions import ServiceLifecycleError


class ServiceState(enum.StrEnum):
    """Estados posibles de un servicio."""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class Service(abc.ABC):
    """Base class for every long-lived component.

    Subclasses implement ``_on_start`` / ``_on_stop``; the public template
    methods manage state transitions and error classification uniformly.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._state = ServiceState.CREATED
        self._logger = logging.getLogger(f"app.{name}")

    @property
    def name(self) -> str:
        """Unique service name (used by watchdog, health monitor and logs)."""
        return self._name

    @property
    def state(self) -> ServiceState:
        """Current lifecycle state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Whether the service is in RUNNING state."""
        return self._state is ServiceState.RUNNING

    @final
    async def start(self) -> None:
        """Start the service (idempotent).

        Raises:
            ServiceLifecycleError: If the underlying start hook fails.
        """
        if self._state is ServiceState.RUNNING:
            return
        self._state = ServiceState.STARTING
        self._logger.info("Starting service '%s'", self._name)
        try:
            await self._on_start()
        except Exception as exc:
            self._state = ServiceState.FAILED
            raise ServiceLifecycleError(
                f"Service '{self._name}' failed to start",
                context={"service": self._name, "error": repr(exc)},
            ) from exc
        self._state = ServiceState.RUNNING
        self._logger.info("Service '%s' is running", self._name)

    @final
    async def stop(self) -> None:
        """Stop the service (idempotent, never raises)."""
        if self._state not in (ServiceState.RUNNING, ServiceState.FAILED):
            return
        self._state = ServiceState.STOPPING
        self._logger.info("Stopping service '%s'", self._name)
        try:
            await self._on_stop()
        except Exception:
            self._logger.exception("Error while stopping service '%s'", self._name)
        self._state = ServiceState.STOPPED
        self._logger.info("Service '%s' stopped", self._name)

    async def healthcheck(self) -> bool:
        """Return whether the service considers itself healthy.

        Subclasses may override with deeper probes (ping, queue depth...).
        """
        return self.is_running

    @abc.abstractmethod
    async def _on_start(self) -> None:
        """Hook: acquire resources and launch background tasks."""

    @abc.abstractmethod
    async def _on_stop(self) -> None:
        """Hook: cancel tasks and release resources gracefully."""

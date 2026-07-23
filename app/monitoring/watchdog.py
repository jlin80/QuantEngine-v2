"""Watchdog: módulos congelados, errores repetitivos y reinicio automático.

Cada componente registrado emite ``heartbeat``; si deja de hacerlo dentro de
su timeout, el watchdog lo marca FROZEN, publica :class:`ModuleFrozen` e
intenta reiniciarlo mediante su callback (con límite de reinicios).
"""

import asyncio
import contextlib
import enum
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.config.settings import WatchdogSettings
from app.core.events.bus import EventBus
from app.core.events.events import ModuleFrozen
from app.core.exceptions import WatchdogError
from app.core.lifecycle import Service

RestartCallback = Callable[[], Awaitable[None]]
"""Corrutina que reinicia el componente (p.ej. ``service.stop``+``start``)."""


class ComponentStatus(enum.StrEnum):
    """Estado observado de un componente."""

    OK = "ok"
    FROZEN = "frozen"
    RESTARTING = "restarting"
    EXHAUSTED = "exhausted"  # reinicios agotados — requiere intervención


@dataclass(slots=True)
class _Component:
    """Estado interno de un componente monitoreado."""

    name: str
    heartbeat_timeout: float
    restart: RestartCallback | None
    max_restarts: int
    error_threshold: int
    error_window: float
    last_heartbeat: float = field(default_factory=time.monotonic)
    status: ComponentStatus = ComponentStatus.OK
    restart_count: int = 0
    error_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))


class Watchdog(Service):
    """Supervisor of registered components.

    Args:
        settings: Watchdog section of the configuration.
        bus: Event bus where incidents are published.
    """

    def __init__(self, settings: WatchdogSettings, bus: EventBus) -> None:
        super().__init__("watchdog")
        self._settings = settings
        self._bus = bus
        self._components: dict[str, _Component] = {}
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("app.watchdog")

    # ------------------------------------------------------------------
    # Registro y señales
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        heartbeat_timeout_seconds: float | None = None,
        restart_callback: RestartCallback | None = None,
        max_restarts: int | None = None,
        error_threshold: int | None = None,
        error_window_seconds: float | None = None,
    ) -> None:
        """Register a component to be supervised.

        Args:
            name: Unique component name.
            heartbeat_timeout_seconds: Silence tolerated before FROZEN.
            restart_callback: Coroutine that restarts the component.
            max_restarts: Automatic restart budget.
            error_threshold: Errors within the window that trigger an alert.
            error_window_seconds: Sliding window for error counting.

        Raises:
            WatchdogError: If the name is already registered.
        """
        if name in self._components:
            raise WatchdogError(f"Component '{name}' already registered", context={"name": name})
        self._components[name] = _Component(
            name=name,
            heartbeat_timeout=heartbeat_timeout_seconds
            or self._settings.default_heartbeat_timeout_seconds,
            restart=restart_callback,
            max_restarts=max_restarts if max_restarts is not None else self._settings.max_restarts,
            error_threshold=(
                error_threshold if error_threshold is not None else self._settings.error_threshold
            ),
            error_window=error_window_seconds or self._settings.error_window_seconds,
        )

    def heartbeat(self, name: str) -> None:
        """Record a liveness signal from a component.

        Raises:
            WatchdogError: If the component is unknown.
        """
        component = self._components.get(name)
        if component is None:
            raise WatchdogError(f"Unknown component '{name}'", context={"name": name})
        component.last_heartbeat = time.monotonic()
        if component.status is ComponentStatus.FROZEN:
            self._log.info("Component '%s' resumed heartbeating", name)
            component.status = ComponentStatus.OK

    def report_error(self, name: str, error: Exception) -> None:
        """Record an error for repetitive-failure detection.

        Args:
            name: Component name (unknown names are tolerated: bus handlers).
            error: The exception observed.
        """
        component = self._components.get(name)
        if component is None:
            return
        now = time.monotonic()
        component.error_times.append(now)
        recent = [t for t in component.error_times if now - t <= component.error_window]
        if len(recent) >= component.error_threshold:
            self._log.error(
                "Component '%s' exceeds error threshold (%d in %.0fs); last: %r",
                name,
                len(recent),
                component.error_window,
                error,
            )
            component.error_times.clear()

    @property
    def component_statuses(self) -> dict[str, ComponentStatus]:
        """Current status per registered component."""
        return {name: c.status for name, c in self._components.items()}

    # ------------------------------------------------------------------
    # Supervisión
    # ------------------------------------------------------------------

    async def _check_loop(self) -> None:
        """Periodically scan components for stale heartbeats."""
        while True:
            await asyncio.sleep(self._settings.check_interval_seconds)
            try:
                await self._scan()
            except Exception:
                self._log.exception("Watchdog scan failed")

    async def _scan(self) -> None:
        """Single pass over every component."""
        now = time.monotonic()
        for component in self._components.values():
            if component.status in (ComponentStatus.RESTARTING, ComponentStatus.EXHAUSTED):
                continue
            silence = now - component.last_heartbeat
            if silence <= component.heartbeat_timeout:
                continue
            self._log.warning(
                "Component '%s' frozen: no heartbeat for %.1fs (timeout %.1fs)",
                component.name,
                silence,
                component.heartbeat_timeout,
            )
            component.status = ComponentStatus.FROZEN
            action = await self._try_restart(component)
            await self._publish_frozen(component, silence, action)

    async def _try_restart(self, component: _Component) -> str:
        """Attempt an automatic restart within the configured budget."""
        if component.restart is None:
            return "none"
        if component.restart_count >= component.max_restarts:
            component.status = ComponentStatus.EXHAUSTED
            self._log.error(
                "Component '%s' exhausted its restart budget (%d)",
                component.name,
                component.max_restarts,
            )
            return "exhausted"
        component.status = ComponentStatus.RESTARTING
        component.restart_count += 1
        try:
            await component.restart()
        except Exception:
            self._log.exception("Restart of component '%s' failed", component.name)
            component.status = ComponentStatus.FROZEN
            return "restart_failed"
        component.last_heartbeat = time.monotonic()
        component.status = ComponentStatus.OK
        self._log.info(
            "Component '%s' restarted (%d/%d)",
            component.name,
            component.restart_count,
            component.max_restarts,
        )
        return "restarted"

    async def _publish_frozen(self, component: _Component, silence: float, action: str) -> None:
        """Publish a ModuleFrozen incident (best effort)."""
        with contextlib.suppress(Exception):
            await self._bus.publish(
                ModuleFrozen(
                    source=self.name,
                    module=component.name,
                    seconds_since_heartbeat=round(silence, 1),
                    action=action,
                )
            )

    async def _on_start(self) -> None:
        self._task = asyncio.create_task(self._check_loop(), name="watchdog")

    async def _on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

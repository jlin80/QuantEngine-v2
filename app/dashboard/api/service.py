"""ApiService: sirve la app FastAPI con uvicorn dentro del motor."""

import asyncio

import uvicorn
from fastapi import FastAPI

from app.config.settings import DashboardSettings
from app.core.lifecycle import Service


class ApiService(Service):
    """Runs uvicorn as a background task under the engine lifecycle.

    Args:
        app: FastAPI application to serve.
        settings: Dashboard section of the configuration.
    """

    def __init__(self, app: FastAPI, settings: DashboardSettings) -> None:
        super().__init__("api")
        config = uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,  # el logging lo gobierna app.logging
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task: asyncio.Task[None] | None = None

    async def _on_start(self) -> None:
        self._task = asyncio.create_task(self._server.serve(), name="uvicorn")
        # Esperar a que el socket esté escuchando (o falle rápido).
        for _ in range(100):
            if self._server.started:
                return
            if self._task.done():
                self._task.result()  # re-lanza la excepción de arranque
                return
            await asyncio.sleep(0.05)

    async def _on_stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            await self._task
            self._task = None

    async def healthcheck(self) -> bool:
        """Healthy while the uvicorn task is alive."""
        return self.is_running and self._task is not None and not self._task.done()

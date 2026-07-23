"""Punto de entrada: ``python -m app``."""

import asyncio
import contextlib
import logging
import signal

from app.config.settings import get_settings
from app.engine.bootstrap import build_container
from app.engine.engine import QuantEngine
from app.logging.setup import configure_logging


async def _run() -> None:
    """Build, start and supervise the engine until shutdown."""
    settings = get_settings()
    configure_logging(settings.logging)
    log = logging.getLogger("app.main")

    container = build_container(settings)
    engine = QuantEngine(settings, container)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # En Windows add_signal_handler no está disponible: se cubre con
        # el KeyboardInterrupt capturado en main().
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, engine.request_stop)

    await engine.start()
    try:
        await engine.run_forever()
    finally:
        await engine.stop()
        log.info("Bye")


def main() -> None:
    """Sync entry point."""
    # Windows / Ctrl+C: el apagado ordenado ya ocurrió en el finally de _run().
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()

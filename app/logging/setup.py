"""Configuración del sistema de logging.

Salidas:
    * Consola (nivel según configuración).
    * ``logs/app.log`` — todo, con rotación.
    * ``logs/errors.log`` — solo ERROR/CRITICAL, con rotación.
    * ``logs/modules/<logger>.log`` — un archivo por módulo declarado en
      la configuración (``logging.module_files``), con rotación.
"""

import logging
import logging.handlers
from pathlib import Path

from app.config.settings import LoggingSettings
from app.logging.formatters import build_formatter
from app.logging.recent import install_recent_errors_buffer


def _rotating_handler(
    path: Path, formatter: logging.Formatter, settings: LoggingSettings, level: int
) -> logging.Handler:
    """Build a size-rotated file handler."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    return handler


def configure_logging(settings: LoggingSettings) -> None:
    """Configure the whole logging tree from settings (idempotent).

    Args:
        settings: Logging section of the central configuration.
    """
    root = logging.getLogger()
    # Idempotencia: limpiar handlers previos (reconfiguración en tests).
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    level = getattr(logging, settings.level.upper(), logging.INFO)
    root.setLevel(level)
    formatter = build_formatter(settings.json_format)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    log_dir = settings.directory
    root.addHandler(_rotating_handler(log_dir / "app.log", formatter, settings, level))
    root.addHandler(_rotating_handler(log_dir / "errors.log", formatter, settings, logging.ERROR))

    # Archivo dedicado por módulo (propagan también a los handlers raíz).
    for logger_name in settings.module_files:
        module_logger = logging.getLogger(logger_name)
        filename = logger_name.removeprefix("app.").replace(".", "_") + ".log"
        already = any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            and Path(h.baseFilename).name == filename
            for h in module_logger.handlers
        )
        if not already:
            module_logger.addHandler(
                _rotating_handler(log_dir / "modules" / filename, formatter, settings, level)
            )

    install_recent_errors_buffer()

    # Silenciar ruido de librerías de terceros.
    for noisy in ("httpx", "httpcore", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

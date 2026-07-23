"""Formateadores de log: texto legible y JSON estructurado."""

import json
import logging
from typing import Any

from app.utils.time import isoformat_utc, utc_now

TEXT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class JsonFormatter(logging.Formatter):
    """Structured JSON formatter (one JSON object per line)."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record as a JSON line.

        Args:
            record: Standard logging record.

        Returns:
            JSON string with timestamp, level, logger, message and extras.
        """
        payload: dict[str, Any] = {
            "timestamp": isoformat_utc(utc_now()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload["context"] = context
        return json.dumps(payload, ensure_ascii=False, default=str)


def build_formatter(json_format: bool) -> logging.Formatter:
    """Return the configured formatter.

    Args:
        json_format: Whether to emit structured JSON lines.

    Returns:
        A ready-to-use :class:`logging.Formatter`.
    """
    if json_format:
        return JsonFormatter()
    return logging.Formatter(TEXT_FORMAT, datefmt=DATE_FORMAT)

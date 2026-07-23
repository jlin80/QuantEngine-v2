"""Ambientes de ejecución soportados."""

import enum
import os

_ENV_VAR = "QE_ENVIRONMENT"


class Environment(enum.StrEnum):
    """Ambientes con configuración independiente."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PAPER = "paper"
    PRODUCTION = "production"


def detect_environment() -> Environment:
    """Resolve the active environment from ``QE_ENVIRONMENT``.

    Returns:
        The configured environment; defaults to ``development``.
    """
    raw = os.environ.get(_ENV_VAR, Environment.DEVELOPMENT.value).strip().lower()
    try:
        return Environment(raw)
    except ValueError:
        return Environment.DEVELOPMENT

"""Fixtures compartidas. Las pruebas SIEMPRE corren en ambiente testing."""

import os

import pytest
from app.config.environment import Environment
from app.config.settings import Settings, get_settings

os.environ.setdefault("QE_ENVIRONMENT", "testing")


@pytest.fixture()
def settings() -> Settings:
    """Settings del ambiente testing (Discord deshabilitado)."""
    get_settings.cache_clear()
    return get_settings(Environment.TESTING)

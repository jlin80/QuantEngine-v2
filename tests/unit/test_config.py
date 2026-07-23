"""Pruebas del sistema de configuración."""

from app.config.environment import Environment, detect_environment
from app.config.settings import Settings, get_settings


def test_testing_environment_disables_discord(settings: Settings):
    assert settings.environment is Environment.TESTING
    assert settings.discord.enabled is False, "testing.env debe forzar Discord OFF"


def test_nested_env_override(monkeypatch):
    monkeypatch.setenv("QE_DATABASE__HOST", "db.example.com")
    monkeypatch.setenv("QE_DATABASE__PORT", "5555")
    get_settings.cache_clear()
    s = get_settings(Environment.TESTING)
    assert s.database.host == "db.example.com"
    assert s.database.port == 5555
    assert "db.example.com:5555" in s.database.dsn
    get_settings.cache_clear()


def test_secrets_are_not_exposed_in_repr(settings: Settings):
    dumped = repr(settings)
    webhook = settings.discord.webhook_url.get_secret_value()
    if webhook:
        assert webhook not in dumped, "SecretStr no debe filtrarse en repr"


def test_detect_environment_defaults_to_development(monkeypatch):
    monkeypatch.delenv("QE_ENVIRONMENT", raising=False)
    assert detect_environment() is Environment.DEVELOPMENT
    monkeypatch.setenv("QE_ENVIRONMENT", "not-a-real-env")
    assert detect_environment() is Environment.DEVELOPMENT


def test_all_sections_present(settings: Settings):
    for section in (
        "trading",
        "broker",
        "database",
        "cache",
        "discord",
        "notion",
        "dashboard",
        "ml",
        "risk",
        "logging",
        "paper",
        "backtesting",
        "health",
        "watchdog",
    ):
        assert hasattr(settings, section)

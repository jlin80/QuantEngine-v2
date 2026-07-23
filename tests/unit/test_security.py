"""Seguridad (Fase 9): validación de config, rate limiter y rotación de secretos."""

from app.config.environment import Environment
from app.config.settings import SecuritySettings, Settings
from app.production.audit import AuditAction, AuditLog
from app.security.config_validator import validate_config, validation_report
from app.security.rate_limiter import RateLimiter
from app.security.secrets import SecretRotationManager

# ----------------------------------------------------------------------
# Config validator
# ----------------------------------------------------------------------


def test_clean_dev_config_has_no_errors():
    settings = Settings(environment=Environment.DEVELOPMENT)
    report = validation_report(settings)
    assert report["errors"] == 0


def test_live_mode_without_production_is_an_error():
    settings = Settings(environment=Environment.DEVELOPMENT)
    settings = settings.model_copy(
        update={"execution": settings.execution.model_copy(update={"mode": "live"})}
    )
    issues = validate_config(settings)
    assert any(i.severity == "error" and i.field == "execution.mode" for i in issues)


def test_production_requires_db_password():
    settings = Settings(environment=Environment.PRODUCTION)
    issues = validate_config(settings)
    fields = {i.field for i in issues if i.severity == "error"}
    assert "database.password" in fields


# ----------------------------------------------------------------------
# Rate limiter
# ----------------------------------------------------------------------


def test_rate_limiter_blocks_after_capacity():
    limiter = RateLimiter(capacity=3, window_seconds=60.0)
    assert [limiter.allow("ip", now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("ip", now=0.0) is False


def test_rate_limiter_refills_over_time():
    limiter = RateLimiter(capacity=2, window_seconds=10.0)
    assert limiter.allow("ip", now=0.0) is True
    assert limiter.allow("ip", now=0.0) is True
    assert limiter.allow("ip", now=0.0) is False
    # Tras una ventana entera, el cubo se repone.
    assert limiter.allow("ip", now=10.0) is True


def test_rate_limiter_is_per_key():
    limiter = RateLimiter(capacity=1, window_seconds=60.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("b", now=0.0) is True
    assert limiter.allow("a", now=0.0) is False


# ----------------------------------------------------------------------
# Secret rotation
# ----------------------------------------------------------------------


def test_unknown_secret_is_due(tmp_path):
    manager = SecretRotationManager(SecuritySettings(), tmp_path / "rot.json")
    statuses = manager.check(["broker.api_key"])
    assert statuses[0].due is True
    assert statuses[0].age_days is None


def test_freshly_rotated_secret_not_due(tmp_path):
    manager = SecretRotationManager(
        SecuritySettings(secret_max_age_days=90.0), tmp_path / "rot.json"
    )
    manager.mark_rotated("broker.api_key")
    statuses = manager.check(["broker.api_key"])
    assert statuses[0].due is False
    assert statuses[0].age_days is not None


def test_due_rotation_is_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    manager = SecretRotationManager(SecuritySettings(), tmp_path / "rot.json", audit)
    manager.check(["broker.api_key"])
    assert audit.recent(action=str(AuditAction.SECRET_ROTATION_DUE))

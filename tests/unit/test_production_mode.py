"""Resolución de modo: 'paper' por todos los caminos salvo la cadena completa."""

import pytest
from app.config.environment import Environment
from app.config.settings import ExecutionSettings, Settings
from app.production.live import ModeResolver

from tests.unit.production_helpers import make_gate, passing_inputs


def _settings(**overrides) -> Settings:
    """Settings con producción habilitada y los overrides pedidos."""
    base = Settings()
    return base.model_copy(update=overrides)


def test_static_floor_is_always_paper():
    """El suelo de settings nunca puede decir live: no conoce el estado vivo."""
    assert ExecutionSettings(mode="live").resolved_mode() == "paper"


def test_mode_rejects_unknown_values():
    """Antes 'lvie' se aceptaba en silencio y no hacía nada."""
    with pytest.raises(ValueError):
        ExecutionSettings(mode="lvie")


def test_default_resolves_to_paper():
    gate, _ = make_gate()
    resolver = ModeResolver(_settings(), gate)
    assert resolver.resolved_mode() == "paper"
    assert "allow_live" in resolver.blocking_reason()


def test_never_evaluated_gate_resolves_to_paper():
    """Sin evaluación no hay live: 'no lo he mirado' no es 'está bien'."""
    settings = _settings(environment=Environment.PRODUCTION)
    settings.production.allow_live = True
    settings.execution.mode = "live"
    gate, _ = make_gate()
    assert ModeResolver(settings, gate).resolved_mode() == "paper"


def test_full_chain_is_required_for_live(tmp_path):
    settings = _settings(environment=Environment.PRODUCTION)
    settings.production.allow_live = True
    settings.execution.mode = "live"
    gate, approvals = make_gate(tmp_path)
    report = gate.evaluate(passing_inputs())
    approvals.grant(actor="jlin", reason="revisado", report_hash=report.report_hash)
    gate.evaluate(passing_inputs())
    resolver = ModeResolver(settings, gate)
    assert resolver.resolved_mode() == "live"
    assert resolver.blocking_reason() == ""


def test_stale_gate_report_falls_back_to_paper(tmp_path):
    """Una aprobación vieja no es una aprobación."""
    settings = _settings(environment=Environment.PRODUCTION)
    settings.production.allow_live = True
    settings.execution.mode = "live"
    gate, approvals = make_gate(tmp_path)
    report = gate.evaluate(passing_inputs())
    approvals.grant(actor="jlin", reason="revisado", report_hash=report.report_hash)
    gate.evaluate(passing_inputs())
    resolver = ModeResolver(settings, gate, max_report_age_seconds=-1.0)
    assert resolver.resolved_mode() == "paper"
    assert "Live Gate" in resolver.blocking_reason()


def test_wrong_environment_falls_back_to_paper(tmp_path):
    settings = _settings(environment=Environment.PAPER)
    settings.production.allow_live = True
    settings.execution.mode = "live"
    gate, approvals = make_gate(tmp_path)
    report = gate.evaluate(passing_inputs())
    approvals.grant(actor="jlin", reason="revisado", report_hash=report.report_hash)
    assert ModeResolver(settings, gate).resolved_mode() == "paper"

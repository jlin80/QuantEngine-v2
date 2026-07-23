"""Live Gate: sin atajos. Cada criterio bloquea por sí solo."""

from app.production.live import GateInputs

from tests.unit.production_helpers import make_gate, passing_inputs


def test_default_state_rejects_and_explains():
    gate, _ = make_gate()
    report = gate.evaluate(GateInputs())
    assert not report.approved
    assert report.failed_count > 0
    # Nunca responde "no" a secas: cada fallo trae su explicación.
    assert all(reason.strip() for reason in report.reasons)


def test_perfect_system_still_needs_the_human():
    gate, _ = make_gate()
    report = gate.evaluate(passing_inputs())
    assert not report.approved
    assert report.reasons == ["Falta la aprobación explícita del operador para este reporte"]


def test_operator_approval_completes_the_chain(tmp_path):
    gate, approvals = make_gate(tmp_path)
    first = gate.evaluate(passing_inputs())
    approvals.grant(actor="jlin", reason="revisado", report_hash=first.report_hash)
    assert gate.evaluate(passing_inputs()).approved


def test_allow_live_false_blocks_even_with_approval(tmp_path):
    """La llave maestra manda: aprobar no sirve si allow_live está cerrado."""
    gate, approvals = make_gate(tmp_path)
    inputs = passing_inputs(allow_live=False)
    report = gate.evaluate(inputs)
    approvals.grant(actor="jlin", reason="fuerzo", report_hash=report.report_hash)
    second = gate.evaluate(inputs)
    assert not second.approved
    assert any("allow_live" in reason for reason in second.reasons)


def test_changing_criteria_invalidates_the_approval(tmp_path):
    """Aprobar el sistema de ayer no aprueba el de hoy."""
    gate, approvals = make_gate(tmp_path, min_profit_factor=1.6)
    report = gate.evaluate(passing_inputs())
    approvals.grant(actor="jlin", reason="ok", report_hash=report.report_hash)
    assert gate.evaluate(passing_inputs()).approved

    # Se relaja un umbral → cambia el hash → la aprobación persistida deja de
    # valer, aunque el nuevo gate lea el mismo fichero de aprobaciones.
    relaxed, _ = make_gate(tmp_path, min_profit_factor=1.0)
    assert not relaxed.evaluate(passing_inputs()).approved


def test_unknown_values_fail_closed():
    """Un criterio desconocido cuenta como incumplido, nunca como cumplido."""
    gate, _ = make_gate()
    report = gate.evaluate(passing_inputs(system_healthy=None, broker_connected=None))
    names = {check.name: check.passed for check in report.checks}
    assert names["system_health"] is False
    assert names["broker"] is False


def test_each_statistical_threshold_blocks_alone(tmp_path):
    gate, approvals = make_gate(tmp_path)
    baseline = gate.evaluate(passing_inputs())
    approvals.grant(actor="jlin", reason="ok", report_hash=baseline.report_hash)

    for override, expected in (
        ({"profit_factor": 1.0}, "profit_factor"),
        ({"sharpe": 0.2}, "sharpe"),
        ({"max_drawdown_pct": 40.0}, "max_drawdown_pct"),
        ({"paper_trades": 3}, "paper_trades"),
        ({"paper_days": 1.0}, "paper_days"),
        ({"test_coverage_pct": 10.0}, "test_coverage_pct"),
        ({"open_critical_errors": 2}, "no_critical_errors"),
    ):
        report = gate.evaluate(passing_inputs(**override))
        failed = {check.name for check in report.checks if not check.passed}
        assert expected in failed
        assert not report.approved


def test_kill_switch_and_safe_mode_veto_on_their_own():
    gate, _ = make_gate()
    assert not gate.evaluate(passing_inputs(kill_switch_active=True)).approved
    assert not gate.evaluate(passing_inputs(safe_mode_active=True)).approved


def test_report_is_json_safe():
    gate, _ = make_gate()
    payload = gate.evaluate(GateInputs()).to_dict()
    assert set(payload) >= {"approved", "checks", "reasons", "report_hash", "evaluated_at"}
    assert isinstance(payload["checks"], list)

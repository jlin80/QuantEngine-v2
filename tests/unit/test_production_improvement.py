"""Continuous Improvement Engine: detecta módulos grandes, TODOs y duplicados."""

from app.config.settings import ImprovementSettings
from app.production.improvement import ContinuousImprovementEngine


def _engine(tmp_path, **overrides) -> ContinuousImprovementEngine:
    settings = ImprovementSettings(scan_dir=tmp_path / "pkg", **overrides)
    (tmp_path / "pkg").mkdir()
    return ContinuousImprovementEngine(settings)


def test_flags_large_module(tmp_path):
    engine = _engine(tmp_path, large_module_loc=10)
    big = tmp_path / "pkg" / "big.py"
    big.write_text("def public():\n" + "    x = 1\n" * 40, encoding="utf-8")
    report = engine.analyze()
    assert any(i.category == "large-module" for i in report.items)


def test_flags_todo(tmp_path):
    engine = _engine(tmp_path, large_module_loc=10_000)
    (tmp_path / "pkg" / "m.py").write_text(
        "def f():\n    pass  # TODO: mejorar esto\n", encoding="utf-8"
    )
    report = engine.analyze()
    todos = [i for i in report.items if i.category == "todo"]
    assert todos and "TODO" in todos[0].detail


def test_flags_duplicate_block(tmp_path):
    engine = _engine(tmp_path, large_module_loc=10_000, duplicate_block_lines=4)
    block = (
        "value = compute_something(alpha, beta)\n"
        "result = transform(value, gamma, delta)\n"
        "checked = validate(result, epsilon)\n"
        "final = persist(checked, zeta, eta)\n"
    )
    (tmp_path / "pkg" / "a.py").write_text(block, encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(block, encoding="utf-8")
    report = engine.analyze()
    assert any(i.category == "duplication" for i in report.items)


def test_report_is_prioritized_and_serializable(tmp_path):
    engine = _engine(tmp_path, large_module_loc=5)
    (tmp_path / "pkg" / "big.py").write_text(
        "def public():\n" + "    x = 1\n" * 30, encoding="utf-8"
    )
    report = engine.analyze()
    top = report.top(3)
    assert top == sorted(top, key=lambda i: i.priority, reverse=True)
    assert "items" in report.to_dict()
    assert report.to_markdown().startswith("Oportunidades detectadas")

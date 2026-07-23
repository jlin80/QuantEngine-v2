"""Report generation endpoints (JSON/Markdown/CSV) built from live engine state."""

import csv
import io
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from app.core.container import Container
from app.dashboard.api.audit import audit_log
from app.execution.api import ExecutionCore
from app.utils.time import utc_now

router = APIRouter(tags=["reports"])

_REPORTS_DIR = Path("logs") / "reports"
_EXT = {"json": "json", "markdown": "md", "csv": "csv"}


def _flatten(prefix: str, data: dict[str, Any], out: dict[str, Any]) -> None:
    """Flatten nested scalar fields into dotted keys.

    Args:
        prefix: Accumulated key prefix.
        data: Nested mapping to flatten.
        out: Destination flat mapping (mutated).
    """
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            _flatten(path, value, out)
        elif value is None or isinstance(value, str | int | float | bool):
            out[path] = value


def _serialize(report: dict[str, Any], fmt: str) -> str:
    """Serialize a report dict to the requested format.

    Args:
        report: The report payload.
        fmt: One of ``json``, ``markdown`` or ``csv``.

    Returns:
        The serialized report text.
    """
    if fmt == "markdown":
        flat: dict[str, Any] = {}
        _flatten("", report, flat)
        lines = [f"# Quant Engine report — {report.get('period', 'custom')}", ""]
        lines.extend(f"- **{key}**: {value}" for key, value in flat.items())
        return "\n".join(lines)
    if fmt == "csv":
        flat = {}
        _flatten("", report, flat)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["key", "value"])
        for key, value in flat.items():
            writer.writerow([key, value])
        return buffer.getvalue()
    return json.dumps(report, indent=2, ensure_ascii=False)


@router.post("/reports/generate")
async def generate_report(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Generate a report from live engine state and persist it (audited)."""
    period = str(payload.get("period", "custom"))
    fmt = str(payload.get("format", "json"))
    report: dict[str, Any] = {"generated_at": utc_now().isoformat(), "period": period}
    container: Container | None = request.app.state.container
    if container is not None and container.contains(ExecutionCore):
        report["execution"] = container.resolve(ExecutionCore).generate_trade_report()
    content = _serialize(report, fmt)
    ext = _EXT.get(fmt, "json")
    filename = f"report_{period}_{utc_now().strftime('%Y%m%dT%H%M%S')}.{ext}"
    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / filename).write_text(content, encoding="utf-8")
    except OSError:
        pass
    audit_log.record(action="report.generate", after={"period": period, "format": fmt})
    return {"report": report, "format": fmt, "filename": filename, "content": content}


@router.get("/reports")
async def list_reports() -> dict[str, Any]:
    """List previously generated report files (newest first)."""
    if not _REPORTS_DIR.exists():
        return {"reports": []}
    files = sorted((path.name for path in _REPORTS_DIR.glob("report_*")), reverse=True)
    return {"reports": files}

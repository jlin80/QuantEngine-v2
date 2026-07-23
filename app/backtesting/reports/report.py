"""Generación de reportes del laboratorio (Fase 6).

Produce reportes en JSON, Markdown y HTML a partir de un
:class:`~app.backtesting.models.BacktestResult`, incluyendo la curva de equity
(sparkline SVG autocontenido), el drawdown, la distribución de operaciones y la
actividad por sesión. El reporte PDF queda como estructura preparada.
"""

import html
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.backtesting.models import BacktestResult, EquityPoint


class ReportError(RuntimeError):
    """A requested report format is not available yet."""


def _sparkline(points: Sequence[EquityPoint], *, width: int = 600, height: int = 120) -> str:
    """Return a self-contained SVG sparkline of the equity curve.

    Args:
        points: Curva de equity.
        width: Ancho del SVG en px.
        height: Alto del SVG en px.

    Returns:
        Cadena SVG (vacía si no hay suficientes puntos).
    """
    if len(points) < 2:
        return ""
    equities = [p.equity for p in points]
    lo = min(equities)
    hi = max(equities)
    span = hi - lo or 1.0
    step = width / (len(points) - 1)
    coords = [
        f"{i * step:.2f},{height - (equity - lo) / span * height:.2f}"
        for i, equity in enumerate(equities)
    ]
    path = " ".join(coords)
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        'preserveAspectRatio="none" role="img" aria-label="Curva de equity">'
        f'<polyline fill="none" stroke="#2b8a3e" stroke-width="2" points="{path}"/>'
        "</svg>"
    )


class ReportGenerator:
    """Render backtest results into several formats."""

    def to_json(self, result: BacktestResult) -> str:
        """Serialize the full result as indented JSON."""
        return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)

    def to_markdown(self, result: BacktestResult) -> str:
        """Render a compact Markdown report."""
        stats = result.statistics
        lines = [
            f"# Backtest — {result.config.label}",
            "",
            f"- **Símbolo**: {result.config.symbol} ({result.config.timeframe})",
            f"- **Velas**: {result.bars}",
            f"- **Balance inicial**: {result.config.initial_balance:.2f}",
            f"- **Balance final**: {result.final_balance:.2f}",
            f"- **Retorno**: {result.return_pct:.2f}%",
            "",
            "## Métricas",
            "",
            "| Métrica | Valor |",
            "| --- | --- |",
        ]
        for key in (
            "total_trades",
            "win_rate",
            "profit_factor",
            "expectancy",
            "expectancy_r",
            "sharpe",
            "sortino",
            "calmar",
            "sqn",
            "mar_ratio",
            "max_drawdown_pct",
            "average_drawdown_pct",
            "max_consecutive_losses",
            "exposure_pct",
        ):
            if key in stats:
                lines.append(f"| {key} | {stats[key]} |")
        lines += ["", "## Distribución por sesión", ""]
        sessions = stats.get("trades_by_session", {})
        if isinstance(sessions, dict):
            for session, count in sessions.items():
                lines.append(f"- {session}: {count}")
        return "\n".join(lines) + "\n"

    def to_html(self, result: BacktestResult) -> str:
        """Render a self-contained HTML report with an equity sparkline."""
        stats = result.statistics
        rows = "".join(
            f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
            for key, value in stats.items()
            if not isinstance(value, dict | list)
        )
        chart = _sparkline(result.equity_curve)
        label = html.escape(result.config.label)
        symbol = html.escape(f"{result.config.symbol} ({result.config.timeframe})")
        return (
            "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<title>Backtest — {label}</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:900px}"
            "table{border-collapse:collapse;width:100%}"
            "td{border:1px solid #ccc;padding:.35rem .6rem}"
            "h1{margin-bottom:.25rem}.muted{color:#666}</style></head><body>"
            f"<h1>Backtest — {label}</h1>"
            f"<p class='muted'>{symbol} · {result.bars} velas · "
            f"retorno {result.return_pct:.2f}%</p>"
            f"<div>{chart}</div>"
            f"<h2>Métricas</h2><table>{rows}</table>"
            "</body></html>"
        )

    def to_pdf(self, result: BacktestResult) -> bytes:
        """Render a PDF report (estructura preparada).

        Raises:
            ReportError: Siempre; el backend PDF aún no está habilitado.
        """
        raise ReportError("El reporte PDF es estructura preparada; usa HTML o Markdown.")

    def save(
        self, result: BacktestResult, directory: Path | str, *, formats: Sequence[str] = ("json",)
    ) -> dict[str, Path]:
        """Write the requested report formats to disk.

        Args:
            result: Resultado del backtest.
            directory: Carpeta de salida.
            formats: Formatos a generar (``json`` | ``md`` | ``html``).

        Returns:
            Mapa formato → ruta escrita.

        Raises:
            ReportError: Si se pide un formato no soportado (p. ej. ``pdf``).
        """
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        renderers: dict[str, tuple[str, Any]] = {
            "json": (".json", self.to_json),
            "md": (".md", self.to_markdown),
            "html": (".html", self.to_html),
        }
        written: dict[str, Path] = {}
        stem = result.config.label.replace(" ", "_") or "backtest"
        for fmt in formats:
            if fmt not in renderers:
                raise ReportError(f"Formato de reporte no soportado: {fmt}")
            suffix, renderer = renderers[fmt]
            path = out / f"{stem}{suffix}"
            path.write_text(renderer(result), encoding="utf-8")
            written[fmt] = path
        return written

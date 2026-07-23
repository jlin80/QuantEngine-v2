"""Continuous Improvement Engine (Fase 9 — modo desarrollo permanente).

El proyecto nunca se considera "terminado". Este motor analiza el código y las
señales de ejecución en busca de oportunidades de mejora y produce una **lista
priorizada**: módulos grandes candidatos a refactor, bloques duplicados, marcas
``TODO``/``FIXME``, módulos sin pruebas y cuellos de botella observados en el
scheduler. No cambia nada: recomienda. Las recomendaciones se auditan y, si
Notion está habilitado, se documentan.
"""

import ast
import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.settings import ImprovementSettings
from app.core.container import Container
from app.scheduler.scheduler import AsyncScheduler
from app.utils.time import utc_now

_log = logging.getLogger("app.production.improvement")
_TODO = re.compile(r"#\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

#: Prioridad base por categoría (0-100). Se ajusta por severidad concreta.
_PRIORITY: dict[str, int] = {
    "bottleneck": 80,
    "missing-tests": 65,
    "duplication": 55,
    "large-module": 45,
    "todo": 30,
}


@dataclass(frozen=True, slots=True)
class Improvement:
    """One prioritized improvement opportunity.

    Attributes:
        category: Kind of finding (``large-module``, ``duplication``...).
        priority: 0-100, higher is more urgent.
        title: One-line summary.
        detail: Human-readable explanation.
        location: File/path the finding anchors to (best effort).
    """

    category: str
    priority: int
    title: str
    detail: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "category": self.category,
            "priority": self.priority,
            "title": self.title,
            "detail": self.detail,
            "location": self.location,
        }


@dataclass(frozen=True, slots=True)
class ImprovementReport:
    """Prioritized list of improvement opportunities."""

    generated_at: str = field(default_factory=lambda: utc_now().isoformat())
    items: list[Improvement] = field(default_factory=list)

    def top(self, n: int = 10) -> list[Improvement]:
        """The ``n`` highest-priority items."""
        return sorted(self.items, key=lambda i: i.priority, reverse=True)[:n]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        by_category: dict[str, int] = defaultdict(int)
        for item in self.items:
            by_category[item.category] += 1
        return {
            "generated_at": self.generated_at,
            "total": len(self.items),
            "by_category": dict(by_category),
            "items": [
                item.to_dict()
                for item in sorted(self.items, key=lambda i: i.priority, reverse=True)
            ],
        }

    def to_markdown(self) -> str:
        """Render the top findings as a Markdown checklist for Notion/docs."""
        lines = [f"Oportunidades detectadas: **{len(self.items)}**.", ""]
        for item in self.top(20):
            loc = f" — `{item.location}`" if item.location else ""
            lines.append(f"- [ ] ({item.priority}) **{item.title}**{loc}: {item.detail}")
        return "\n".join(lines)


class ContinuousImprovementEngine:
    """Analyze code and runtime signals; emit a prioritized improvement list.

    Args:
        settings: Improvement configuration.
        container: DI container for runtime signals (``None`` for static only).
    """

    def __init__(self, settings: ImprovementSettings, container: Container | None = None) -> None:
        self._settings = settings
        self._container = container
        self._last_report: ImprovementReport | None = None

    @property
    def last_report(self) -> ImprovementReport | None:
        """Most recent analysis, or ``None`` if never run."""
        return self._last_report

    def analyze(self) -> ImprovementReport:
        """Run every analyzer and build the prioritized report."""
        items: list[Improvement] = []
        py_files = self._python_files()
        items.extend(self._large_modules(py_files))
        items.extend(self._todos(py_files))
        items.extend(self._duplicates(py_files))
        items.extend(self._missing_tests(py_files))
        items.extend(self._bottlenecks())
        report = ImprovementReport(items=items)
        self._last_report = report
        _log.info("Continuous improvement: %d opportunities found", len(items))
        return report

    # ------------------------------------------------------------------
    # Analizadores estáticos
    # ------------------------------------------------------------------

    def _python_files(self) -> list[Path]:
        """Every ``.py`` file under the scan dir, excluding caches."""
        scan = self._settings.scan_dir
        if not scan.exists():
            return []
        return [path for path in scan.rglob("*.py") if "__pycache__" not in path.parts]

    def _large_modules(self, files: list[Path]) -> list[Improvement]:
        """Flag modules above the LOC threshold as refactor candidates."""
        threshold = self._settings.large_module_loc
        found: list[Improvement] = []
        for path in files:
            try:
                loc = sum(1 for _ in path.open("r", encoding="utf-8"))
            except OSError:
                continue
            if loc > threshold:
                # Prioridad escala con cuánto sobrepasa el umbral (tope +30).
                extra = min(30, (loc - threshold) // 40)
                found.append(
                    Improvement(
                        category="large-module",
                        priority=_PRIORITY["large-module"] + extra,
                        title=f"Módulo grande ({loc} LOC)",
                        detail=f"Supera {threshold} LOC; considerar dividir responsabilidades.",
                        location=str(path),
                    )
                )
        return found

    def _todos(self, files: list[Path]) -> list[Improvement]:
        """Collect TODO/FIXME/XXX/HACK markers."""
        found: list[Improvement] = []
        for path in files:
            try:
                for lineno, line in enumerate(path.open("r", encoding="utf-8"), start=1):
                    if _TODO.search(line):
                        found.append(
                            Improvement(
                                category="todo",
                                priority=_PRIORITY["todo"],
                                title="Marca pendiente en el código",
                                detail=line.strip()[:160],
                                location=f"{path}:{lineno}",
                            )
                        )
            except OSError:
                continue
        return found

    def _duplicates(self, files: list[Path]) -> list[Improvement]:
        """Detect repeated blocks of normalized code across the codebase."""
        window = max(4, self._settings.duplicate_block_lines)
        seen: dict[str, str] = {}
        reported: set[str] = set()
        found: list[Improvement] = []
        for path in files:
            try:
                lines = [ln.strip() for ln in path.open("r", encoding="utf-8")]
            except OSError:
                continue
            meaningful = [
                (i, ln) for i, ln in enumerate(lines, start=1) if ln and not ln.startswith("#")
            ]
            for idx in range(len(meaningful) - window + 1):
                block = "\n".join(ln for _, ln in meaningful[idx : idx + window])
                if len(block) < window * 8:  # bloques triviales no cuentan
                    continue
                key = hashlib.sha1(block.encode("utf-8")).hexdigest()
                first_line = meaningful[idx][0]
                origin = f"{path}:{first_line}"
                if key in seen and seen[key] != origin and key not in reported:
                    reported.add(key)
                    found.append(
                        Improvement(
                            category="duplication",
                            priority=_PRIORITY["duplication"],
                            title=f"Bloque duplicado ({window} líneas)",
                            detail=f"Repetido en {seen[key]} y {origin}; extraer helper.",
                            location=origin,
                        )
                    )
                seen.setdefault(key, origin)
        return found

    def _missing_tests(self, files: list[Path]) -> list[Improvement]:
        """Flag non-trivial modules with classes/functions but no obvious test.

        Heurística barata: para un módulo ``app/x/y.py`` busca cualquier fichero
        de test cuyo nombre contenga ``y``. No pretende ser preciso; señala
        candidatos para revisar.
        """
        project_root = self._settings.scan_dir.parent
        tests_dir = project_root / "tests"
        if not tests_dir.exists():
            return []
        test_names = {p.stem for p in tests_dir.rglob("test_*.py")}
        found: list[Improvement] = []
        for path in files:
            stem = path.stem
            if stem in ("__init__", "__main__") or not self._has_public_api(path):
                continue
            if not any(stem in name for name in test_names):
                found.append(
                    Improvement(
                        category="missing-tests",
                        priority=_PRIORITY["missing-tests"],
                        title="Módulo sin pruebas evidentes",
                        detail=f"No se encontró un test que referencie '{stem}'.",
                        location=str(path),
                    )
                )
        return found

    @staticmethod
    def _has_public_api(path: Path) -> bool:
        """Whether a module defines public classes or functions worth testing."""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return False
        for node in tree.body:
            if isinstance(
                node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ) and not node.name.startswith("_"):
                return True
        return False

    # ------------------------------------------------------------------
    # Señales de runtime
    # ------------------------------------------------------------------

    def _bottlenecks(self) -> list[Improvement]:
        """Flag scheduler jobs whose error rate suggests a bottleneck."""
        if self._container is None or not self._container.contains(AsyncScheduler):
            return []
        found: list[Improvement] = []
        for job in self._container.resolve(AsyncScheduler).jobs:
            if job.error_count > 0 and job.run_count >= 0:
                total = job.run_count + job.error_count
                rate = job.error_count / total if total else 1.0
                if rate >= 0.2:
                    found.append(
                        Improvement(
                            category="bottleneck",
                            priority=_PRIORITY["bottleneck"],
                            title=f"Job inestable: {job.name}",
                            detail=(
                                f"{job.error_count} errores de {total} ejecuciones "
                                f"({rate * 100:.0f}%). Último: {job.last_error[:120]}"
                            ),
                            location=f"scheduler:{job.name}",
                        )
                    )
        return found

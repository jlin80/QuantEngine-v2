"""Historial append-only de corridas bayesianas (Fase 10).

Guarda cada optimización (mejor score, evaluaciones, mejores parámetros) para
comparar resultados entre corridas y evidenciar la mejora. Nunca reescribe: el
conocimiento no se pierde.
"""

import json
from pathlib import Path
from typing import Any

from app.research.bayesian_lab.optimizer import BayesianResult
from app.research.models import new_id
from app.utils.time import isoformat_utc, utc_now


class BayesianHistory:
    """Append-only store of Bayesian optimization runs.

    Args:
        directory: Carpeta de persistencia; ``None`` mantiene el historial sólo
            en memoria (tests).
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = directory / "history.jsonl" if directory is not None else None
        self._runs: list[dict[str, Any]] = []
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        """Load any persisted runs into memory."""
        if self._path is None or not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                self._runs.append(json.loads(line))

    def record(self, result: BayesianResult, *, label: str, context: str = "") -> dict[str, Any]:
        """Append a run to the history.

        Args:
            result: Resultado de la optimización bayesiana.
            label: Etiqueta legible de la corrida.
            context: Contexto opcional (símbolo, genoma, hipótesis...).

        Returns:
            El registro guardado (JSON-safe).
        """
        entry: dict[str, Any] = {
            "run_id": new_id("bayes"),
            "label": label,
            "context": context,
            "objective": result.objective,
            "best_score": result.best_score if result.best_score > float("-inf") else None,
            "evaluations": result.evaluations,
            "best_params": result.best_params,
            "recorded_at": isoformat_utc(utc_now()),
        }
        self._runs.append(entry)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        return entry

    def runs(self) -> list[dict[str, Any]]:
        """Every recorded run (oldest first)."""
        return list(self._runs)

    def count(self) -> int:
        """Number of recorded runs."""
        return len(self._runs)

    def compare(self) -> dict[str, Any]:
        """Summarize progress across runs (first vs best vs latest).

        Returns:
            Resumen con la mejor corrida, la última y la mejora relativa frente a
            la primera — la evidencia de que la búsqueda mejora con el tiempo.
        """
        scored = [r for r in self._runs if isinstance(r.get("best_score"), (int, float))]
        if not scored:
            return {"count": len(self._runs), "best": None, "latest": None, "improvement": 0.0}
        best = max(scored, key=lambda r: float(r["best_score"]))
        first = scored[0]
        latest = scored[-1]
        first_score = float(first["best_score"])
        improvement = float(best["best_score"]) - first_score
        return {
            "count": len(self._runs),
            "best": best,
            "latest": latest,
            "improvement": round(improvement, 6),
            "improved": best["run_id"] != first["run_id"],
        }

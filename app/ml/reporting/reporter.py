"""Generación de reportes del ML para dashboard y Discord (Fase 7).

Compone los ``to_dict`` de los distintos módulos en reportes coherentes (ficha de
modelo, ranking de estrategias, deriva, AutoML) y un resumen en Markdown. No
calcula nada nuevo: sólo presenta la evidencia ya producida.
"""

from collections.abc import Sequence
from typing import Any

from app.ml.registry.records import ModelRecord
from app.ml.services.strategy_intelligence import StrategyScore


class MLReporter:
    """Build JSON/Markdown reports from ML artefacts."""

    @staticmethod
    def model_card(record: ModelRecord) -> dict[str, Any]:
        """Human-facing card for a registered model."""
        return {
            "id": record.id,
            "type": record.model_type,
            "version": record.version,
            "state": record.state.value,
            "active": record.active,
            "metrics": record.metrics,
            "dataset": record.dataset,
            "created_at": record.created_at,
            "result": record.result,
        }

    @staticmethod
    def ranking_report(scores: Sequence[StrategyScore]) -> dict[str, Any]:
        """Strategy ranking summary."""
        return {
            "count": len(scores),
            "ranking": [
                {
                    "rank": i + 1,
                    "name": s.name,
                    "score": round(s.score, 2),
                    "trades": s.trades,
                    "expectancy_r": round(s.historical.expectancy_r, 4),
                    "profit_factor": round(s.historical.profit_factor, 4),
                }
                for i, s in enumerate(scores)
            ],
        }

    @staticmethod
    def markdown_summary(status: dict[str, Any]) -> str:
        """Compact Markdown summary of the ML layer status."""
        registry = status.get("registry", {})
        active = registry.get("active") or {}
        lines = [
            "# Estado del ML (Fase 7)",
            "",
            f"- Modelos registrados: **{registry.get('count', 0)}**",
            f"- Modelo activo: **{active.get('type', '—')}** "
            f"(AUC {active.get('metrics', {}).get('auc', '—')})",
            f"- Deriva activa: **{status.get('drift', {}).get('has_drift', False)}**",
            f"- Estrategias evaluadas: **{status.get('meta', {}).get('strategies', 0)}**",
            "",
            "> El ML asesora; el Decision Engine decide. Solo paper trading.",
        ]
        return "\n".join(lines)

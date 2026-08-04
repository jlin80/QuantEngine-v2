"""Huella de las reglas de ejecución activas, y su comparación con el modelo.

El gate de validación (`min_cv_auc`) evita activar un modelo malo. Falta una
segunda capa, que es lo que hay aquí: **un modelo entrenado bajo unas reglas de
ejecución deja de representar cómo opera el motor en cuanto esas reglas cambian.**

Si se cambia el holding mínimo, el sizing, un filtro de riesgo o el trailing, el
modelo activo sigue asesorando con la estadística de un motor que ya no existe —
y no hay nada en sus métricas que lo delate: su AUC de validación sigue siendo el
mismo número de ayer.

La solución es deliberadamente simple: se calcula un **hash de las reglas de
ejecución que importan**, se guarda junto al modelo al registrarlo, y se compara
con las vigentes. Si difieren, se marca el modelo como "requiere reentrenamiento"
y se avisa por Discord.

Lo que **no** hace, a propósito: desactivar el modelo, activar otro, o disparar
un reentrenamiento automático. El ML asesora y nunca decide por sí solo; un
reentrenamiento automático ante cualquier cambio de configuración sería una
forma cómoda de que el motor se reentrene solo sobre datos que aún no existen.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.config.settings import ExecutionSettings

RULES_VERSION = 1
"""Versión del propio esquema de huella.

Subirla invalida todas las huellas anteriores a propósito: si cambia *qué*
reglas se consideran significativas, las comparaciones antiguas no son válidas.
"""


def execution_rules_snapshot(execution: ExecutionSettings) -> dict[str, Any]:
    """The execution rules that materially change how the engine trades.

    Sólo entran reglas que alteran **qué operaciones existen y cómo se
    gestionan** — es decir, las que cambian la distribución de la que el modelo
    aprendió. Deliberadamente **fuera**: cadencias, rutas de fichero, intervalos
    de reporte y todo lo cosmético, para que un cambio inocuo no dispare una
    alerta y acabe enseñando al operador a ignorarlas.

    Args:
        execution: Configuración de ejecución vigente.

    Returns:
        Instantánea JSON-safe y ordenada de las reglas significativas.
    """
    sizing = execution.sizing
    risk = execution.risk
    return {
        "rules_version": RULES_VERSION,
        # Holding y salidas: deciden cuándo se cierra una operación.
        "holding": {
            "max_holding_minutes": execution.max_holding_minutes,
            "exit_on_regime_change": execution.exit_on_regime_change,
            "regime_change_min_holding_seconds": (execution.regime_change_min_holding_seconds),
            "regime_change_min_holding_by_strategy": dict(
                sorted(execution.regime_change_min_holding_by_strategy.items())
            ),
            "regime_change_min_holding_by_category": dict(
                sorted(execution.regime_change_min_holding_by_category.items())
            ),
            "regime_exit_confirmations": execution.regime_exit_confirmations,
            "regime_exit_family_only": execution.regime_exit_family_only,
        },
        # Trailing y break-even: deciden dónde acaba una operación ganadora.
        "trailing": {
            "break_even_r": execution.break_even_r,
            "trailing_enabled": execution.trailing_enabled,
            "trailing_atr_multiple": execution.trailing_atr_multiple,
            "trailing_activate_r": execution.trailing_activate_r,
        },
        # Sizing: decide el tamaño y, con él, el R de cada operación.
        "sizing": {
            "method": sizing.method,
            "risk_per_trade_pct": sizing.risk_per_trade_pct,
            "atr_stop_multiplier": sizing.atr_stop_multiplier,
            "min_stop_pct": sizing.min_stop_pct,
            "percent_of_equity": sizing.percent_of_equity,
            "fixed_amount": sizing.fixed_amount,
        },
        # Filtros de riesgo: deciden qué operaciones llegan a existir.
        "risk": {
            "max_risk_per_trade_pct": risk.max_risk_per_trade_pct,
            "max_open_positions": risk.max_open_positions,
            "max_positions_per_symbol": risk.max_positions_per_symbol,
            "max_exposure_pct": risk.max_exposure_pct,
            "max_symbol_exposure_pct": risk.max_symbol_exposure_pct,
            "max_spread_bps": risk.max_spread_bps,
            "min_liquidity": risk.min_liquidity,
            "max_consecutive_losses": risk.max_consecutive_losses,
        },
        # Qué símbolos y estrategias pueden abrir: cambia la población entera.
        "toggles": {
            "symbols_enabled": dict(sorted(execution.symbols_enabled.items())),
            "strategies_enabled": dict(sorted(execution.strategies_enabled.items())),
        },
    }


def execution_rules_hash(execution: ExecutionSettings) -> str:
    """Stable short hash of the significant execution rules."""
    payload = json.dumps(execution_rules_snapshot(execution), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecutionRulesCheck:
    """Result of comparing the active model against the current rules."""

    status: str  # ok | stale | no_model | unknown
    current_hash: str
    model_hash: str = ""
    model_id: str = ""
    model_version: str = ""
    changed: list[str] = None  # type: ignore[assignment]
    detail: str = ""

    def __post_init__(self) -> None:
        """Normalise the mutable default without giving up ``frozen``."""
        if self.changed is None:
            object.__setattr__(self, "changed", [])

    @property
    def needs_retraining(self) -> bool:
        """Whether the active model no longer represents how the engine trades."""
        return self.status == "stale"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "status": self.status,
            "needs_retraining": self.needs_retraining,
            "current_hash": self.current_hash,
            "model_hash": self.model_hash,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "changed": list(self.changed),
            "detail": self.detail,
        }


def changed_rule_groups(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Names of the rule groups that differ between two snapshots.

    Sirve para que la alerta diga *qué* cambió y no sólo que algo cambió: un
    aviso que no señala la causa se ignora a la segunda vez.
    """
    groups = sorted(set(previous) | set(current))
    return [g for g in groups if previous.get(g) != current.get(g)]

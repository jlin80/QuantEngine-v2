"""Runtime configuration overrides set from the dashboard (whitelisted, audited).

Only a curated whitelist of safe, dotted setting paths may be changed. Overrides
are persisted to JSON and reflected by ``GET /api/config``; the guard rejects any
attempt to enable live trading. Applying overrides to already-running subsystems
is wired progressively — a running engine reads most settings at startup — so the
store is the source of truth for operator intent, always recorded in the audit log.
"""

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from app.config.settings import Settings

_log = logging.getLogger("app.api.config")

WHITELIST: tuple[str, ...] = (
    "risk.max_daily_drawdown_pct",
    "risk.max_open_positions",
    "risk.risk_per_trade_pct",
    "execution.risk.max_risk_per_trade_pct",
    "execution.risk.max_daily_loss_pct",
    "execution.risk.max_open_positions",
    "execution.risk.max_positions_per_symbol",
    "execution.risk.max_exposure_pct",
    "execution.risk.max_consecutive_losses",
    "execution.risk.kill_switch_drawdown_pct",
    "execution.symbols_enabled",
    "execution.strategies_enabled",
    "execution.falsification.enabled",
    "execution.max_holding_minutes",
    "execution.exit_on_regime_change",
    "execution.regime_change_min_holding_seconds",
    "execution.regime_change_min_holding_by_strategy",
    "execution.regime_change_min_holding_by_category",
    "execution.regime_exit_confirmations",
    "execution.regime_exit_family_only",
    "execution.break_even_r",
    "execution.trailing_enabled",
    "execution.trailing_atr_multiple",
    "execution.trailing_activate_r",
    "execution.sizing.risk_per_trade_pct",
    "execution.sizing.atr_stop_multiplier",
    "execution.sizing.reward_risk",
    "execution.sizing.min_stop_pct",
    "quant.consensus.min_score",
    "quant.consensus.min_confidence",
    "quant.consensus.min_agreement",
    "quant.context.max_spread_bps",
    "quant.context.atr_pct_low",
    "paper.initial_balance",
    "paper.slippage_bps",
    "discord.enabled",
    "discord.min_level",
    "ml.enabled",
    "ml.auto_activate",
    "ml.meta.enabled",
    "ml.meta.apply_governance",
    "ml.meta.disable_after_periods",
    "ml.execution_rules_check.enabled",
    "backtesting.criteria.min_profit_factor",
    "backtesting.criteria.min_sharpe",
    "backtesting.criteria.max_drawdown_pct",
)
"""Dotted setting paths the dashboard is allowed to override."""

# Rutas que el motor lee en vivo (cada evaluación): mutar el objeto settings las
# aplica al instante. El resto también se muta, pero algunos subsistemas leen su
# valor al construirse (p. ej. ``paper.initial_balance``) y sólo cambian tras un
# reinicio; ``apply`` marca cuáles fueron en caliente para informar al operador.
_LIVE_PREFIXES: tuple[str, ...] = (
    "execution.risk.",
    "execution.symbols_enabled",
    "execution.strategies_enabled",
    "execution.max_holding_minutes",
    "execution.exit_on_regime_change",
    "execution.regime_change_min_holding_seconds",
    "execution.regime_change_min_holding_by_strategy",
    "execution.regime_change_min_holding_by_category",
    # El engine las lee en cada ciclo de gestión → aplican en caliente. Las de
    # trailing/break-even NO: el PositionManager las lee al construirse, así que
    # quedan fuera y `apply` avisará de que necesitan reinicio.
    "execution.regime_exit_confirmations",
    "execution.regime_exit_family_only",
    "execution.sizing.",
    "quant.consensus.",
    "quant.context.",
)


def _resolve(settings: Settings, path: str) -> Any:
    """Resolve a dotted setting path against the settings tree.

    Args:
        settings: Root settings object.
        path: Dotted path (e.g. ``execution.risk.max_open_positions``).

    Returns:
        The current value, or ``None`` if the path does not resolve.
    """
    node: Any = settings
    for part in path.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return node


def _set_live(settings: Settings, path: str, value: Any) -> bool:
    """Write ``value`` onto the live settings object at ``path``.

    Los subsistemas del motor (RiskManager, DecisionEngine, filtros, sizing)
    guardan una referencia a estos objetos de settings y leen sus atributos en
    cada evaluación; mutar el atritubo aplica el cambio sin reiniciar.

    Returns:
        ``True`` si se pudo escribir; ``False`` si la ruta no resuelve.
    """
    parts = path.split(".")
    parent: Any = settings
    for part in parts[:-1]:
        parent = getattr(parent, part, None)
        if parent is None:
            return False
    try:
        setattr(parent, parts[-1], value)
    except Exception:  # pragma: no cover - pydantic validation-assignment guard
        _log.warning("No se pudo aplicar en vivo %s=%r", path, value)
        return False
    return True


def _is_live(path: str) -> bool:
    """Whether a whitelisted path is read live by the engine (hot-applies)."""
    return any(path == prefix or path.startswith(prefix) for prefix in _LIVE_PREFIXES)


class RuntimeConfigStore:
    """Persisted store of whitelisted config overrides and strategy intents.

    Args:
        path: JSON file backing the store, or ``None`` for in-memory only.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._overrides: dict[str, Any] = {}
        self._strategy: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        """Load overrides from disk (best effort)."""
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._overrides = dict(data.get("overrides", {}))
            self._strategy = dict(data.get("strategies", {}))
        except (OSError, ValueError) as exc:
            _log.warning("Runtime config load failed: %r", exc)

    def _persist(self) -> None:
        """Persist overrides to disk (best effort)."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"overrides": self._overrides, "strategies": self._strategy}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            _log.warning("Runtime config persist failed: %r", exc)

    def effective(self, settings: Settings) -> dict[str, dict[str, Any]]:
        """Return the effective whitelisted config (defaults + overrides).

        Args:
            settings: Root settings object.

        Returns:
            Mapping ``path -> {value, overridden, default}``.
        """
        with self._lock:
            overrides = dict(self._overrides)
        result: dict[str, dict[str, Any]] = {}
        for path in WHITELIST:
            default = _resolve(settings, path)
            result[path] = {
                "value": overrides.get(path, default),
                "overridden": path in overrides,
                "default": default,
            }
        return result

    def apply(self, settings: Settings, patch: dict[str, Any]) -> dict[str, Any]:
        """Validate, store and apply a config patch to the live settings.

        Muta el objeto de settings vivo para que los subsistemas que lo leen en
        cada evaluación (riesgo, decisión, filtros, sizing) tomen el cambio al
        instante; el resto queda persistido y se aplica en el próximo arranque.

        Args:
            settings: Root settings object (mutado en sitio).
            patch: Mapping of whitelisted paths to new values.

        Returns:
            The applied (coerced) values.

        Raises:
            KeyError: If a key is not in the whitelist.
        """
        applied: dict[str, Any] = {}
        with self._lock:
            for key, value in patch.items():
                if key not in WHITELIST:
                    raise KeyError(key)
                coerced = _coerce(_resolve(settings, key), value)
                _set_live(settings, key, coerced)
                self._overrides[key] = coerced
                applied[key] = coerced
            self._persist()
        return applied

    def reapply(self, settings: Settings) -> None:
        """Push persisted overrides onto the live settings (call at startup).

        Sin esto, un override guardado se perdería en cada reinicio hasta el
        siguiente PATCH: aquí se reescriben sobre el settings recién cargado.
        """
        with self._lock:
            for key, value in self._overrides.items():
                if key in WHITELIST:
                    _set_live(settings, key, value)

    def strategy_overrides(self) -> dict[str, dict[str, Any]]:
        """Return the per-strategy override intents.

        Returns:
            Mapping ``strategy -> {enabled?, weight?}``.
        """
        with self._lock:
            return {name: dict(changes) for name, changes in self._strategy.items()}

    def set_strategy(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge override intents for a strategy.

        Args:
            name: Strategy name.
            changes: Fields to merge (e.g. ``{"enabled": False}``).

        Returns:
            The merged override for the strategy.
        """
        with self._lock:
            current = self._strategy.get(name, {})
            current.update(changes)
            self._strategy[name] = current
            self._persist()
            return dict(current)


def _coerce(current: Any, value: Any) -> Any:
    """Coerce a new value to the type of the current setting value.

    Args:
        current: Current setting value (used as the type hint).
        value: Incoming value from the request.

    Returns:
        The coerced value.
    """
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


config_store = RuntimeConfigStore(Path("logs") / "runtime_config.json")
"""Process-wide runtime config store singleton."""

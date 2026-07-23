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
    "execution.risk.max_exposure_pct",
    "execution.risk.kill_switch_drawdown_pct",
    "paper.initial_balance",
    "paper.slippage_bps",
    "discord.enabled",
    "discord.min_level",
    "ml.enabled",
    "ml.auto_activate",
    "ml.meta.enabled",
    "backtesting.criteria.min_profit_factor",
    "backtesting.criteria.min_sharpe",
    "backtesting.criteria.max_drawdown_pct",
)
"""Dotted setting paths the dashboard is allowed to override."""


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
        """Validate and store a config patch.

        Args:
            settings: Root settings object (used for type coercion).
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
                self._overrides[key] = coerced
                applied[key] = coerced
            self._persist()
        return applied

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

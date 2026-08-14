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
from typing import Any, get_origin

from pydantic import TypeAdapter, ValidationError

from app.config.settings import Settings

_log = logging.getLogger("app.api.config")

WHITELIST: tuple[str, ...] = (
    # `risk.*` (el `RiskSettings` raíz) estuvo aquí y no lo lee NADIE: el propio
    # modelo se documenta como "contratos para fases futuras". Eran tres campos
    # editables con nombres casi idénticos a los `execution.risk.*` de al lado,
    # que sí mandan — bajar ahí el drawdown máximo no cambiaba nada y parecía
    # que sí. El freno diario real es `quant.filters.max_drawdown_pct`, que no
    # estaba en la whitelist y ahora sí (lo lee el DrawdownFilter al construir
    # la cadena, así que exige reinicio y `apply` lo reporta).
    "quant.filters.max_drawdown_pct",
    "execution.risk.max_risk_per_trade_pct",
    "execution.risk.max_daily_loss_pct",
    "execution.risk.max_open_positions",
    "execution.risk.max_positions_per_symbol",
    "execution.risk.max_exposure_pct",
    "execution.risk.max_consecutive_losses",
    "execution.risk.kill_switch_drawdown_pct",
    # Toggle de operador: deja de parar por drawdown (kill switch, Safe Mode y
    # filtro de drawdown diario). Va bajo `execution.risk.` → aplica en caliente.
    "execution.risk.ignore_drawdown_limits",
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
    # `atr_pct_high` faltaba en la whitelist: se podia ajustar el umbral bajo en
    # caliente pero no el alto, que era justo el que estaba mal calibrado.
    "quant.context.atr_pct_high",
    "quant.context.atr_pct_low_by_symbol",
    "quant.context.atr_pct_high_by_symbol",
    "paper.initial_balance",
    "paper.slippage_bps",
    "discord.enabled",
    "discord.min_level",
    "ml.enabled",
    "ml.auto_activate",
    # Ventana semanal de entrenamiento. El scheduler registra el job al
    # arrancar, así que cambiarla exige reinicio y `apply` lo reporta.
    "ml.training.weekly_enabled",
    "ml.training.weekly_weekday",
    "ml.training.weekly_hour_utc",
    "ml.training.weekly_window_hours",
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
        self.load_error: str | None = None
        """Motivo por el que no se pudo leer el fichero, o ``None`` si todo fue bien."""
        self._load()

    def _load(self) -> None:
        """Load overrides from disk, recording any failure.

        Se lee con ``utf-8-sig``: en Windows es fácil que una herramienta deje
        un BOM al principio (``Set-Content -Encoding utf8`` de PowerShell 5.1 lo
        hace), y con ``utf-8`` a secas el BOM rompe el ``json.loads`` y tira el
        fichero **entero**. Pasó de verdad el 13/08: el motor arrancó sin ningún
        override del operador — incluidos los frenos de riesgo.

        Un fallo ya no se traga en silencio: queda en :attr:`load_error`, que el
        guard de arranque convierte en abortar en `paper`/`production`. Arrancar
        con la configuración vacía es indistinguible de arrancar bien, y la
        diferencia son los límites de pérdida.
        """
        self.load_error = None
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
            self._overrides = dict(data.get("overrides", {}))
            self._strategy = dict(data.get("strategies", {}))
        except (OSError, ValueError) as exc:
            self.load_error = f"{self._path}: {exc}"
            _log.error(
                "Runtime config ILEGIBLE (%s). El motor no debe operar sin la "
                "configuración del operador: se conserva el fichero tal cual.",
                self.load_error,
            )

    def _persist(self) -> None:
        """Persist overrides to disk (best effort).

        Se niega a escribir mientras haya un ``load_error`` sin resolver. Sin
        esto, un fichero corrupto más cualquier cambio desde el dashboard
        sobreescribiría la configuración real con la vacía que se pudo cargar —
        el fichero es la única copia, así que la pérdida sería definitiva.
        """
        if self._path is None:
            return
        if self.load_error is not None:
            _log.error(
                "No se persiste la configuración: el fichero de origen no se pudo "
                "leer (%s). Sobrescribirlo perdería los overrides existentes.",
                self.load_error,
            )
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

        Se publica también el ``kind`` declarado y si el path aplica en
        caliente. El frontend deducía el control del ``typeof`` del valor por
        defecto, y un `dict` vacío es indistinguible de un objeto cualquiera:
        los campos compuestos acababan en un input de texto que mostraba
        ``[object Object]``. El tipo lo sabe el esquema — que lo diga él.

        Returns:
            Mapping ``path -> {value, overridden, default, kind, live}``.
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
                "kind": _kind(_annotation(settings, path), default),
                "live": _is_live(path),
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

        El parche es **todo o nada**: se valida entero antes de escribir nada.
        Aplicar la mitad de un parche deja al motor en un estado que el operador
        no pidió y que la UI no refleja — peor que rechazarlo completo.

        Returns:
            The applied (validated) values.

        Raises:
            KeyError: If a key is not in the whitelist.
            ValueError: Si algún valor no encaja con el tipo declarado, o si no
                se pudo escribir sobre los settings vivos.
        """
        with self._lock:
            validated: dict[str, Any] = {}
            for key, value in patch.items():
                if key not in WHITELIST:
                    raise KeyError(key)
                validated[key] = _coerce(settings, key, value)
            for key, coerced in validated.items():
                if not _set_live(settings, key, coerced):
                    raise ValueError(f"{key}: no se pudo aplicar sobre la configuración viva")
                self._overrides[key] = coerced
            self._persist()
        return validated

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


def _kind(annotation: Any, current: Any) -> str:
    """Clasificar un path para que el dashboard elija el control adecuado.

    Se mira primero la anotación declarada y sólo se cae al valor actual cuando
    no hay esquema que consultar.
    """
    target = annotation if annotation is not None else type(current)
    origin = get_origin(target) or target
    if origin is bool:
        return "bool"
    if origin in (int, float):
        return "number"
    if origin is dict:
        return "dict"
    if origin is list:
        return "list"
    return "string"


def _annotation(settings: Settings, path: str) -> Any:
    """Anotación de tipo declarada para un path, o ``None`` si no se puede leer.

    Se prefiere al tipo del **valor actual** porque un `dict` vacío no dice nada
    sobre lo que acepta: `symbols_enabled` empieza en `{}` y de ahí no se deduce
    que sus valores sean booleanos.
    """
    parts = path.split(".")
    parent: Any = settings
    for part in parts[:-1]:
        parent = getattr(parent, part, None)
        if parent is None:
            return None
    fields = getattr(type(parent), "model_fields", None)
    if not isinstance(fields, dict):
        return None
    field = fields.get(parts[-1])
    return None if field is None else field.annotation


def _coerce(settings: Settings, path: str, value: Any) -> Any:
    """Validar y convertir un valor entrante contra el esquema declarado.

    Los settings anidados **no** llevan ``validate_assignment``, así que un
    ``setattr`` acepta cualquier cosa sin chistar. Sin esta puerta, guardar un
    campo de tipo `dict` desde el dashboard escribía la cadena `"[object
    Object]"` dentro de `execution.symbols_enabled`, y el siguiente tick moría
    con ``AttributeError`` al llamar ``.get()`` sobre un `str` — en el camino
    caliente de ejecución. Validar aquí es lo que hace que el Config Center no
    pueda tumbar el motor.

    Args:
        settings: Root settings object.
        path: Dotted path que se está escribiendo.
        value: Valor entrante de la petición (puede venir como JSON en texto).

    Returns:
        El valor validado y convertido al tipo declarado.

    Raises:
        ValueError: Si el valor no encaja con el tipo declarado.
    """
    annotation = _annotation(settings, path)
    if annotation is None:
        return value
    # Los campos compuestos llegan como texto desde un input; un dict/lista ya
    # decodificado pasa de largo.
    if isinstance(value, str):
        origin = get_origin(annotation)
        if origin in (dict, list) or annotation in (dict, list):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError(f"{path}: se esperaba JSON válido ({exc})") from exc
    try:
        return TypeAdapter(annotation).validate_python(value)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'valor'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ValueError(f"{path}: {errors}") from exc


config_store = RuntimeConfigStore(Path("logs") / "runtime_config.json")
"""Process-wide runtime config store singleton."""

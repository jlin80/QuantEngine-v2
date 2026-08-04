"""Falsación automática del cambio de holding por estrategia (Bloque 7.1).

"Se desplegó sin errores" no es evidencia de que un cambio funcione. El Bloque 1
hizo una predicción concreta y comprobable, y este módulo la mide sola en la
ventana posterior al cambio en vez de esperar a que alguien se acuerde de
mirarlo:

1. ``take_profit`` **sube del 0 %** — si ninguna operación llega al objetivo, el
   holding sigue cortando la tesis antes de tiempo y el cambio no sirvió.
2. ``regime_change`` **baja del 80 %** — era el síntoma original.
3. La **duración mediana** se acerca a la esperada por estrategia.

El veredicto se publica **acierte o falle**: una predicción que sólo se reporta
cuando se cumple no es una falsación, es una felicitación.

El módulo no conoce el Event Bus ni Discord: devuelve el veredicto y el motor lo
publica. Igual que el gestor de experimentos del Bloque 2.
"""

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import ExecutionSettings, FalsificationSettings
from app.execution.models import TradeRecord
from app.utils.time import utc_now

_LOG = logging.getLogger("app.execution.falsification")

_REGIME_CHANGE = "regime_change"
_TAKE_PROFIT = "take_profit"


@dataclass(frozen=True, kw_only=True, slots=True)
class FalsificationVerdict:
    """Outcome of measuring the Bloque-1 prediction over its window.

    ``outcome`` es ``confirmed`` (las tres predicciones se cumplen),
    ``refuted`` (alguna falla) o ``pending`` (muestra insuficiente: la ventana
    se extiende en vez de concluir con ruido).
    """

    outcome: str
    trades: int
    window_hours: float
    take_profit_pct: float
    regime_change_pct: float
    median_holding_seconds: float
    expected_holding_seconds: float
    checks: dict[str, bool]
    detail: str

    @property
    def confirmed(self) -> bool:
        """Whether every prediction held."""
        return self.outcome == "confirmed"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "outcome": self.outcome,
            "trades": self.trades,
            "window_hours": self.window_hours,
            "take_profit_pct": self.take_profit_pct,
            "regime_change_pct": self.regime_change_pct,
            "median_holding_seconds": self.median_holding_seconds,
            "expected_holding_seconds": self.expected_holding_seconds,
            "checks": self.checks,
            "detail": self.detail,
        }


class HoldingChangeFalsifier:
    """Measure, in the window after the change, whether Bloque 1 worked.

    Args:
        settings: Configuración de la falsación.
        execution: Configuración de ejecución, para conocer el holding esperado
            de cada estrategia y poder comparar la duración observada.
    """

    def __init__(self, settings: FalsificationSettings, execution: ExecutionSettings) -> None:
        self._settings = settings
        self._execution = execution
        self._started_at: datetime | None = None
        self._deadline: datetime | None = None
        self._verdict: FalsificationVerdict | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def verdict(self) -> FalsificationVerdict | None:
        """The verdict already emitted, if any."""
        return self._verdict

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        self._load_once()
        return {
            "enabled": self._settings.enabled,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "deadline": self._deadline.isoformat() if self._deadline else None,
            "verdict": self._verdict.to_dict() if self._verdict else None,
        }

    def start(self, now: datetime | None = None) -> bool:
        """Open the measurement window (idempotente y persistente).

        Returns:
            ``True`` si se abrió en esta llamada.
        """
        if not self._settings.enabled:
            return False
        self._load_once()
        if self._started_at is not None:
            return False
        moment = now or utc_now()
        self._started_at = moment
        self._deadline = moment + timedelta(hours=self._settings.window_hours)
        self._append(
            {"kind": "started", "at": moment.isoformat(), "deadline": self._deadline.isoformat()}
        )
        _LOG.info("Falsación del holding abierta (corte %s)", self._deadline)
        return True

    # ------------------------------------------------------------------
    # Veredicto
    # ------------------------------------------------------------------

    def evaluate(
        self, trades: list[TradeRecord], now: datetime | None = None
    ) -> FalsificationVerdict | None:
        """Emit the verdict once the window has elapsed.

        Args:
            trades: Trade Journal completo.
            now: Momento de referencia (inyectable en tests).

        Returns:
            El veredicto, o ``None`` si la ventana sigue abierta o ya concluyó.
        """
        if not self._settings.enabled:
            return None
        self._load_once()
        if self._started_at is None or self._deadline is None or self._verdict is not None:
            return None
        moment = now or utc_now()
        if moment < self._deadline:
            return None

        window = [t for t in trades if t.exit_time >= self._started_at]
        window_hours = (moment - self._started_at).total_seconds() / 3600.0
        count = len(window)

        if count < self._settings.min_trades:
            self._deadline = moment + timedelta(hours=self._settings.extension_hours)
            verdict = FalsificationVerdict(
                outcome="pending",
                trades=count,
                window_hours=round(window_hours, 2),
                take_profit_pct=0.0,
                regime_change_pct=0.0,
                median_holding_seconds=0.0,
                expected_holding_seconds=0.0,
                checks={},
                detail=(
                    f"Sólo {count} operaciones en {window_hours:.0f}h (mínimo "
                    f"{self._settings.min_trades}). Ventana extendida "
                    f"{self._settings.extension_hours:.0f}h; aún no se puede afirmar nada."
                ),
            )
            self._append({"kind": "pending", **verdict.to_dict()})
            return verdict

        take_profit_pct = self._share(window, _TAKE_PROFIT)
        regime_change_pct = self._share(window, _REGIME_CHANGE)
        median_holding = statistics.median(t.duration_seconds for t in window)
        expected = self._expected_holding(window)

        checks = {
            "take_profit_above_zero": take_profit_pct > self._settings.min_take_profit_pct,
            "regime_change_below_threshold": (
                regime_change_pct < self._settings.max_regime_change_pct
            ),
            "median_duration_near_expected": self._duration_ok(median_holding, expected),
        }
        passed = all(checks.values())
        verdict = FalsificationVerdict(
            outcome="confirmed" if passed else "refuted",
            trades=count,
            window_hours=round(window_hours, 2),
            take_profit_pct=round(take_profit_pct, 2),
            regime_change_pct=round(regime_change_pct, 2),
            median_holding_seconds=round(median_holding, 1),
            expected_holding_seconds=round(expected, 1),
            checks=checks,
            detail=self._detail(passed, checks, take_profit_pct, regime_change_pct),
        )
        self._verdict = verdict
        self._append({"kind": "verdict", **verdict.to_dict()})
        return verdict

    # ------------------------------------------------------------------
    # Cálculo
    # ------------------------------------------------------------------

    @staticmethod
    def _share(trades: list[TradeRecord], reason: str) -> float:
        """Percentage of trades closed for a given reason."""
        if not trades:
            return 0.0
        hits = sum(1 for t in trades if t.exit_reason.value == reason)
        return hits / len(trades) * 100.0

    def _expected_holding(self, trades: list[TradeRecord]) -> float:
        """Mean of the holding thresholds that actually applied to the window.

        No es un número global: cada operación tiene el suyo según su
        estrategia, que es justo lo que introdujo el Bloque 1.
        """
        thresholds = [
            self._execution.min_holding_seconds_for(t.strategy, t.strategy_category) for t in trades
        ]
        return sum(thresholds) / len(thresholds) if thresholds else 0.0

    def _duration_ok(self, median: float, expected: float) -> bool:
        """Whether the observed median is close enough to the expected one.

        Sólo se exige que **no se quede corta**: pasarse de largo lo acota ya el
        límite global de 4h, y no es el fallo que este cambio corregía.
        """
        if expected <= 0:
            return True
        return median >= expected * (1.0 - self._settings.duration_tolerance)

    def _detail(
        self,
        passed: bool,
        checks: dict[str, bool],
        take_profit_pct: float,
        regime_change_pct: float,
    ) -> str:
        """Human-readable verdict, naming what failed."""
        if passed:
            return (
                f"El cambio de holding por estrategia se comporta como se predijo: "
                f"take_profit {take_profit_pct:.1f}% (>0), regime_change "
                f"{regime_change_pct:.1f}% (<{self._settings.max_regime_change_pct:.0f}%) "
                f"y la duración mediana alcanza lo esperado."
            )
        failed = [name for name, ok in checks.items() if not ok]
        return (
            f"El cambio **no** produjo lo que se predijo. Falla: {', '.join(failed)}. "
            f"take_profit {take_profit_pct:.1f}%, regime_change {regime_change_pct:.1f}%. "
            f"Conviene revisar los umbrales por estrategia antes de darlo por bueno."
        )

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _append(self, payload: dict[str, Any]) -> None:
        """Append one auditable line (best effort, nunca lanza)."""
        if not self._settings.persist:
            return
        try:
            path = Path(self._settings.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"recorded_at": utc_now().isoformat(), **payload}, default=str)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            _LOG.exception("No se pudo registrar la falsación")

    def _load_once(self) -> None:
        """Rehydrate the window from disk so a restart does not reset it."""
        if self._loaded:
            return
        self._loaded = True
        if not self._settings.persist:
            return
        path = Path(self._settings.state_path)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            _LOG.exception("No se pudo releer el registro de falsación")
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = data.get("kind")
            if kind == "started":
                self._started_at = datetime.fromisoformat(str(data["at"]))
                self._deadline = datetime.fromisoformat(str(data["deadline"]))
            elif kind == "verdict":
                self._verdict = FalsificationVerdict(
                    outcome=str(data["outcome"]),
                    trades=int(data["trades"]),
                    window_hours=float(data["window_hours"]),
                    take_profit_pct=float(data["take_profit_pct"]),
                    regime_change_pct=float(data["regime_change_pct"]),
                    median_holding_seconds=float(data["median_holding_seconds"]),
                    expected_holding_seconds=float(data["expected_holding_seconds"]),
                    checks=dict(data.get("checks", {})),
                    detail=str(data.get("detail", "")),
                )

"""Experimentos con fecha de corte sobre estrategias en observación.

`atr_expansion` y `mean_reversion` llevan R negativo consistente. La decisión
de apagarlas no debería depender de que el operador se acuerde de revisarlas
dentro de tres días: se abre un **experimento con fecha de corte** y, al
vencer, el sistema mide la expectativa de la estrategia con las operaciones
cerradas *dentro de la ventana* y emite un veredicto.

Regla dura, deliberada: **nada de esto desactiva una estrategia**. El veredicto
publica un evento (que Discord convierte en aviso con los números) y se anota
en un registro append-only. Apagar una estrategia sigue siendo mover
``execution.strategies_enabled`` a mano.

Por qué la ventana empieza cuando empieza el experimento y no antes: el
experimento existe para juzgar a la estrategia **bajo las reglas nuevas** (el
holding por estrategia del Bloque 1). Mezclar operaciones anteriores al cambio
mediría justo lo que el cambio pretendía arreglar.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import StrategyExperimentSettings
from app.execution.models import TradeRecord
from app.utils.time import utc_now

_LOG = logging.getLogger("app.execution.experiments")


@dataclass(kw_only=True, slots=True)
class StrategyExperiment:
    """Un experimento abierto sobre una estrategia.

    Attributes:
        strategy: Estrategia observada (minúsculas).
        started_at: Inicio de la ventana de medición (UTC).
        deadline: Fecha de corte vigente (UTC); se extiende si falta muestra.
        extensions: Cuántas veces se extendió por muestra insuficiente.
        closed: Si ya se emitió un veredicto definitivo.
    """

    strategy: str
    started_at: datetime
    deadline: datetime
    extensions: int = 0
    closed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "started_at": self.started_at.isoformat(),
            "deadline": self.deadline.isoformat(),
            "extensions": self.extensions,
            "closed": self.closed,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ExperimentVerdict:
    """Resultado de evaluar un experimento vencido.

    ``outcome`` es uno de:

    - ``deactivation_candidate``: muestra suficiente y expectativa por debajo
      del umbral. Se propone apagarla — no se apaga.
    - ``passed``: la estrategia se recuperó dentro de la ventana; experimento
      cerrado sin acción.
    - ``extended``: no hubo operaciones suficientes; la ventana se alarga.
    """

    strategy: str
    outcome: str
    trades: int
    expectancy_r: float
    win_rate: float
    total_r: float
    window_hours: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "strategy": self.strategy,
            "outcome": self.outcome,
            "trades": self.trades,
            "expectancy_r": self.expectancy_r,
            "win_rate": self.win_rate,
            "total_r": self.total_r,
            "window_hours": self.window_hours,
            "detail": self.detail,
        }


class StrategyExperimentManager:
    """Open, persist and adjudicate cut-off experiments on strategies.

    No conoce el Event Bus ni Discord: devuelve veredictos y el llamante
    (el motor) los publica. Así se puede probar de forma síncrona y aislada.

    Args:
        settings: Configuración de los experimentos.
    """

    def __init__(self, settings: StrategyExperimentSettings) -> None:
        self._settings = settings
        self._experiments: dict[str, StrategyExperiment] = {}
        self._verdicts: list[ExperimentVerdict] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def experiments(self) -> list[StrategyExperiment]:
        """Snapshot of every known experiment (open and closed)."""
        return list(self._experiments.values())

    @property
    def verdicts(self) -> list[ExperimentVerdict]:
        """Verdicts emitted in this process."""
        return list(self._verdicts)

    def candidates(self) -> list[str]:
        """Strategies currently proposed for deactivation."""
        return [v.strategy for v in self._verdicts if v.outcome == "deactivation_candidate"]

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "watching": list(self._settings.watching),
            "deadline_hours": self._settings.deadline_hours,
            "min_trades": self._settings.min_trades,
            "max_expectancy_r": self._settings.max_expectancy_r,
            "experiments": [e.to_dict() for e in self._experiments.values()],
            "candidates": self.candidates(),
        }

    # ------------------------------------------------------------------
    # Apertura
    # ------------------------------------------------------------------

    def open_pending(self, now: datetime | None = None) -> list[StrategyExperiment]:
        """Open an experiment for every watched strategy that lacks one.

        Idempotente: una estrategia ya observada no reinicia su reloj, ni
        siquiera tras reiniciar el motor (el inicio se relee del registro).

        Args:
            now: Momento de referencia (inyectable en tests).

        Returns:
            Los experimentos abiertos en esta llamada (vacío si ninguno).
        """
        if not self._settings.enabled:
            return []
        self._load_once()
        moment = now or utc_now()
        opened: list[StrategyExperiment] = []
        for raw in self._settings.watching:
            strategy = raw.strip().lower()
            if not strategy or strategy in self._experiments:
                continue
            experiment = StrategyExperiment(
                strategy=strategy,
                started_at=moment,
                deadline=moment + timedelta(hours=self._settings.deadline_hours),
            )
            self._experiments[strategy] = experiment
            self._append({"kind": "opened", **experiment.to_dict()})
            opened.append(experiment)
            _LOG.info("Experimento abierto sobre %s (corte %s)", strategy, experiment.deadline)
        return opened

    # ------------------------------------------------------------------
    # Veredicto
    # ------------------------------------------------------------------

    def evaluate(
        self, trades: list[TradeRecord], now: datetime | None = None
    ) -> list[ExperimentVerdict]:
        """Adjudicate every experiment whose deadline has passed.

        Args:
            trades: Operaciones cerradas conocidas (Trade Journal completo).
            now: Momento de referencia (inyectable en tests).

        Returns:
            Los veredictos emitidos en esta pasada.
        """
        if not self._settings.enabled:
            return []
        self._load_once()
        moment = now or utc_now()
        emitted: list[ExperimentVerdict] = []
        for experiment in self._experiments.values():
            if experiment.closed or moment < experiment.deadline:
                continue
            verdict = self._adjudicate(experiment, trades, moment)
            self._verdicts.append(verdict)
            # El estado resultante viaja con el veredicto: al rehidratar hace
            # falta la fecha de corte ya extendida, no la original.
            self._append({"kind": "verdict", **verdict.to_dict(), "state": experiment.to_dict()})
            emitted.append(verdict)
        return emitted

    def _adjudicate(
        self, experiment: StrategyExperiment, trades: list[TradeRecord], now: datetime
    ) -> ExperimentVerdict:
        """Score one expired experiment and update its state."""
        window = [
            t
            for t in trades
            if t.strategy.strip().lower() == experiment.strategy
            and t.exit_time >= experiment.started_at
        ]
        window_hours = (now - experiment.started_at).total_seconds() / 3600.0
        total_r = sum(t.r_multiple for t in window)
        count = len(window)
        expectancy = total_r / count if count else 0.0
        wins = sum(1 for t in window if t.r_multiple > 0)
        win_rate = wins / count if count else 0.0

        if count < self._settings.min_trades:
            experiment.deadline = now + timedelta(hours=self._settings.extension_hours)
            experiment.extensions += 1
            outcome = "extended"
            detail = (
                f"Sólo {count} operaciones en {window_hours:.0f}h "
                f"(mínimo {self._settings.min_trades}). Ventana extendida "
                f"{self._settings.extension_hours:.0f}h hasta "
                f"{experiment.deadline.isoformat()}."
            )
        elif expectancy > self._settings.max_expectancy_r:
            experiment.closed = True
            outcome = "passed"
            detail = (
                f"Se recuperó: {expectancy:+.3f}R de expectativa en {count} "
                f"operaciones ({window_hours:.0f}h). Experimento cerrado sin acción."
            )
        else:
            experiment.closed = True
            outcome = "deactivation_candidate"
            detail = (
                f"Sigue en negativo tras {window_hours:.0f}h: {expectancy:+.3f}R "
                f"de expectativa en {count} operaciones ({total_r:+.2f}R acumulados, "
                f"win rate {win_rate * 100:.0f}%). Propuesta: desactivarla con "
                f"QE_EXECUTION__STRATEGIES_ENABLED. **No se ha desactivado nada** — "
                f"la decisión es tuya."
            )
        return ExperimentVerdict(
            strategy=experiment.strategy,
            outcome=outcome,
            trades=count,
            expectancy_r=round(expectancy, 4),
            win_rate=round(win_rate, 4),
            total_r=round(total_r, 4),
            window_hours=round(window_hours, 2),
            detail=detail,
        )

    # ------------------------------------------------------------------
    # Persistencia append-only
    # ------------------------------------------------------------------

    def _append(self, payload: dict[str, Any]) -> None:
        """Append one auditable line (best effort, nunca lanza)."""
        if not self._settings.persist:
            return
        try:
            path = Path(self._settings.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"at": utc_now().isoformat(), **payload}, default=str)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            _LOG.exception("No se pudo registrar el experimento")

    def _load_once(self) -> None:
        """Rehydrate open experiments from the append-only log.

        Sin esto, un reinicio del motor reiniciaría el reloj de cada
        experimento y la fecha de corte no llegaría nunca.
        """
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
            _LOG.exception("No se pudo releer el registro de experimentos")
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            strategy = str(data.get("strategy", "")).strip().lower()
            if not strategy:
                continue
            kind = data.get("kind")
            if kind == "opened":
                self._experiments[strategy] = StrategyExperiment(
                    strategy=strategy,
                    started_at=datetime.fromisoformat(str(data["started_at"])),
                    deadline=datetime.fromisoformat(str(data["deadline"])),
                    extensions=int(data.get("extensions", 0)),
                    closed=bool(data.get("closed", False)),
                )
            elif kind == "verdict":
                experiment = self._experiments.get(strategy)
                state = data.get("state")
                if experiment is None or not isinstance(state, dict):
                    continue
                experiment.deadline = datetime.fromisoformat(str(state["deadline"]))
                experiment.extensions = int(state.get("extensions", 0))
                experiment.closed = bool(state.get("closed", False))

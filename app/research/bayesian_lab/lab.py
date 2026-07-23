"""Bayesian Lab: une el TPE con el historial de corridas (Fase 10)."""

from pathlib import Path

from app.backtesting.optimizer.space import ParameterSpace
from app.config.settings import BayesianLabSettings
from app.research.bayesian_lab.history import BayesianHistory
from app.research.bayesian_lab.optimizer import BayesianResult, Objective, TPEOptimizer


class BayesianLab:
    """Bayesian optimization with a persisted, comparable history.

    Args:
        settings: Configuración del laboratorio bayesiano.
        history_dir: Carpeta del historial (``None`` = sólo memoria).
    """

    def __init__(self, settings: BayesianLabSettings, *, history_dir: Path | None = None) -> None:
        self._settings = settings
        self._optimizer = TPEOptimizer(settings)
        self._history = BayesianHistory(history_dir)

    @property
    def history(self) -> BayesianHistory:
        """The run history store."""
        return self._history

    def optimize(
        self,
        space: ParameterSpace,
        evaluate: Objective,
        *,
        label: str,
        objective: str = "score",
        context: str = "",
    ) -> BayesianResult:
        """Run a TPE optimization and record it in the history.

        Args:
            space: Espacio de parámetros.
            evaluate: Función objetivo a maximizar.
            label: Etiqueta de la corrida.
            objective: Nombre informativo de la métrica.
            context: Contexto opcional (símbolo/genoma/hipótesis).

        Returns:
            El resultado de la optimización bayesiana.
        """
        optimizer = TPEOptimizer(self._settings, objective=objective)
        result = optimizer.optimize(space, evaluate)
        self._history.record(result, label=label, context=context)
        return result

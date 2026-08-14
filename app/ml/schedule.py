"""Puerta temporal del entrenamiento: decide *cuándo* se entrena, no *cómo*.

El entrenamiento del ML compite por CPU con el motor que está operando, así que
interesa llevarlo al fin de semana con el mercado cerrado. El scheduler del
proyecto es por intervalo y **reinicia su reloj en cada arranque**, de modo que
un job con `interval_seconds` de 7 días no dispararía nunca en una máquina que
se reinicia más a menudo. Por eso el job tiquea seguido (cada 30 min por
defecto) y la decisión vive aquí, contra la última ejecución persistida.

Separado del :class:`~app.ml.api.MLEngine` a propósito: la política de cuándo
entrenar es una regla de calendario, comprobable con un reloj falso y sin tocar
modelos, datasets ni disco de registro.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import MLTrainingSettings

_log = logging.getLogger("app.ml.schedule")


class WeeklyTrainingGate:
    """Decide si toca entrenar, y recuerda cuándo se entrenó por última vez.

    Args:
        settings: Configuración de entrenamiento (ventana y ruta de estado).
    """

    def __init__(self, settings: MLTrainingSettings) -> None:
        self._settings = settings
        self._path = Path(settings.schedule_state_path)
        self._last_run: datetime | None = self._load()

    # ------------------------------------------------------------------
    # Estado persistido
    # ------------------------------------------------------------------

    def _load(self) -> datetime | None:
        """Read the last training timestamp (best effort).

        Un estado ilegible no puede impedir que se entrene: se registra y se
        trata como "nunca se entrenó", que es el lado seguro — como mucho se
        entrena una vez de más.
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw = data.get("last_run_at")
            return datetime.fromisoformat(str(raw)) if raw else None
        except (OSError, ValueError) as exc:
            _log.warning("Estado del calendario de entrenamiento ilegible: %r", exc)
            return None

    def mark_ran(self, now: datetime) -> None:
        """Persist ``now`` as the last training run."""
        self._last_run = now
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"last_run_at": now.isoformat()}, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            _log.warning("No se pudo persistir el calendario de entrenamiento: %r", exc)

    @property
    def last_run(self) -> datetime | None:
        """Timestamp of the last recorded training run."""
        return self._last_run

    # ------------------------------------------------------------------
    # Decisión
    # ------------------------------------------------------------------

    def _in_window(self, now: datetime) -> bool:
        """Whether ``now`` falls inside the configured weekly window."""
        cfg = self._settings
        if now.weekday() != cfg.weekly_weekday:
            return False
        start = now.replace(hour=cfg.weekly_hour_utc, minute=0, second=0, microsecond=0)
        return start <= now < start + timedelta(hours=cfg.weekly_window_hours)

    def decide(self, now: datetime) -> dict[str, Any]:
        """Decide whether training should run at ``now``.

        Devuelve siempre el motivo, no sólo el booleano: un job que se salta en
        silencio es indistinguible de uno que está roto, y este en concreto sólo
        actúa una vez por semana — sin el motivo, dos semanas sin entrenar
        parecerían normales.

        Args:
            now: Instante actual (UTC).

        Returns:
            ``{"run": bool, "reason": str, "kind": "scheduled"|"catchup"|None}``.
        """
        now = now.astimezone(UTC)
        last = self._last_run

        if self._in_window(now):
            # Una sola ejecución por ventana: sin esto, un tick cada 30 min
            # entrenaría 24 veces cada sábado.
            if last is not None and self._in_window(last) and last.date() == now.date():
                return {
                    "run": False,
                    "reason": "ya se entrenó en esta ventana",
                    "kind": None,
                }
            return {"run": True, "reason": "ventana semanal", "kind": "scheduled"}

        stale_after = timedelta(days=self._settings.weekly_max_staleness_days)
        if last is None:
            # Primer arranque fuera de ventana: se espera al fin de semana en
            # vez de entrenar al instante. Entrenar aquí sería justo lo que este
            # cambio viene a evitar — carga de CPU con el mercado abierto.
            return {
                "run": False,
                "reason": "sin entrenamiento previo; se espera a la ventana semanal",
                "kind": None,
            }
        if now - last > stale_after:
            return {
                "run": True,
                "reason": (
                    f"recuperación: {(now - last).days} días sin entrenar "
                    f"(máximo {self._settings.weekly_max_staleness_days:g})"
                ),
                "kind": "catchup",
            }
        return {"run": False, "reason": "fuera de la ventana semanal", "kind": None}

"""Model Registry: versionado, estados, activación y rollback (Fase 7).

Guarda la ficha de cada modelo (metadatos en disco, objeto vivo en memoria),
nunca sobrescribe versiones y mantiene el historial completo. La activación es
reversible: ``rollback`` restaura al instante el modelo anterior. Un modelo
activo sólo **asesora**; jamás abre operaciones por sí mismo.
"""

import json
import logging
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from app.core.exceptions import ModelRegistryError
from app.ml.interfaces.model import Model
from app.ml.registry.records import ModelRecord, ModelState
from app.utils.time import utc_now


class ModelRegistry:
    """Versioned catalogue of trained models with reversible activation.

    Args:
        directory: Carpeta de persistencia de metadatos (``None`` = sólo memoria).
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory
        self._records: dict[str, ModelRecord] = {}
        self._order: list[str] = []
        self._models: dict[str, Model] = {}
        self._active_id: str | None = None
        self._activation_stack: deque[str] = deque(maxlen=50)
        self._audit: list[dict[str, Any]] = []
        self._log = logging.getLogger("app.ml.registry")
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    def register(
        self,
        model: Model,
        *,
        metrics: dict[str, float],
        dataset: dict[str, Any],
        feature_names: list[str],
        params: dict[str, Any] | None = None,
        author: str = "system",
        result: str = "",
        state: ModelState = ModelState.EVALUATED,
    ) -> ModelRecord:
        """Register a new model version (never overwrites an existing one)."""
        model_id = uuid.uuid4().hex[:12]
        record = ModelRecord(
            id=model_id,
            version=self._next_version(model.model_type.value),
            model_type=model.model_type.value,
            dataset=dataset,
            params=params if params is not None else model.params(),
            metrics=dict(metrics),
            state=state,
            author=author,
            result=result,
            feature_names=feature_names,
        )
        self._records[model_id] = record
        self._order.append(model_id)
        self._models[model_id] = model
        self._log_audit("register", model_id, {"version": record.version})
        self._persist()
        return record

    def approve(self, model_id: str, *, reasons: str = "") -> ModelRecord:
        """Mark a model as approved (passed the validation gate)."""
        record = self.get(model_id)
        record.state = ModelState.APPROVED
        if reasons:
            record.result = reasons
        self._log_audit("approve", model_id, {"reasons": reasons})
        self._persist()
        return record

    def reject(self, model_id: str, *, reasons: str = "") -> ModelRecord:
        """Mark a model as rejected (failed the validation gate)."""
        record = self.get(model_id)
        record.state = ModelState.REJECTED
        record.result = reasons
        self._log_audit("reject", model_id, {"reasons": reasons})
        self._persist()
        return record

    # ------------------------------------------------------------------
    # Activación / rollback
    # ------------------------------------------------------------------

    def activate(self, model_id: str) -> ModelRecord:
        """Make a model the active one (previous active is archived).

        Raises:
            ModelRegistryError: Si el modelo no existe.
        """
        record = self.get(model_id)
        previous = self._active_id
        if previous is not None and previous in self._records and previous != model_id:
            self._records[previous].active = False
            self._records[previous].state = ModelState.ARCHIVED
            self._activation_stack.append(previous)
        record.active = True
        record.state = ModelState.ACTIVE
        self._active_id = model_id
        self._log_audit("activate", model_id, {"previous": previous})
        self._persist()
        return record

    def rollback(self) -> ModelRecord | None:
        """Restore the previously active model (immediate rollback)."""
        if not self._activation_stack:
            return None
        restore_id = self._activation_stack.pop()
        if restore_id not in self._records:
            return None
        if self._active_id is not None and self._active_id in self._records:
            self._records[self._active_id].active = False
            self._records[self._active_id].state = ModelState.ARCHIVED
        restored = self._records[restore_id]
        restored.active = True
        restored.state = ModelState.ACTIVE
        previous = self._active_id
        self._active_id = restore_id
        self._log_audit("rollback", restore_id, {"from": previous})
        self._persist()
        return restored

    # ------------------------------------------------------------------
    # Lecturas
    # ------------------------------------------------------------------

    def get(self, model_id: str) -> ModelRecord:
        """Return a record by id.

        Raises:
            ModelRegistryError: Si el id es desconocido.
        """
        record = self._records.get(model_id)
        if record is None:
            raise ModelRegistryError(
                f"Modelo desconocido: '{model_id}'", context={"model_id": model_id}
            )
        return record

    def model_of(self, model_id: str) -> Model | None:
        """Return the live model object for an id (if held in memory)."""
        return self._models.get(model_id)

    def active_record(self) -> ModelRecord | None:
        """The currently active model record (or ``None``)."""
        return self._records.get(self._active_id) if self._active_id else None

    def active_model(self) -> Model | None:
        """The currently active model object (or ``None``)."""
        return self._models.get(self._active_id) if self._active_id else None

    def records(self) -> list[ModelRecord]:
        """Every record in registration order."""
        return [self._records[mid] for mid in self._order]

    def history(self) -> list[dict[str, Any]]:
        """Append-only audit trail of registry decisions."""
        return list(self._audit)

    def count(self) -> int:
        """Number of registered models."""
        return len(self._records)

    def status(self) -> dict[str, Any]:
        """Compact registry status for the dashboard."""
        active = self.active_record()
        return {
            "count": self.count(),
            "active": active.to_dict() if active else None,
            "can_rollback": len(self._activation_stack) > 0,
            "by_state": self._counts_by_state(),
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _next_version(self, model_type: str) -> str:
        """Next version string for a model type (``1.0``, ``2.0``...)."""
        existing = sum(1 for r in self._records.values() if r.model_type == model_type)
        return f"{existing + 1}.0"

    def _counts_by_state(self) -> dict[str, int]:
        """Count of records per state."""
        counts: dict[str, int] = {}
        for record in self._records.values():
            counts[record.state.value] = counts.get(record.state.value, 0) + 1
        return counts

    def _log_audit(self, action: str, model_id: str, detail: dict[str, Any]) -> None:
        """Append an entry to the in-memory audit trail."""
        self._audit.append(
            {
                "action": action,
                "model_id": model_id,
                "at": utc_now().isoformat(),
                "detail": detail,
            }
        )

    def _persist(self) -> None:
        """Write the current snapshot to disk (best-effort)."""
        if self._dir is None:
            return
        snapshot = {
            "records": [self._records[mid].to_dict() for mid in self._order],
            "active_id": self._active_id,
            "activation_stack": list(self._activation_stack),
        }
        try:
            (self._dir / "records.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with (self._dir / "history.jsonl").open("a", encoding="utf-8") as handle:
                if self._audit:
                    handle.write(json.dumps(self._audit[-1], ensure_ascii=False) + "\n")
        except OSError as exc:  # el disco nunca debe tumbar el registro
            self._log.error("No se pudo persistir el registro de modelos: %r", exc)

    def _load(self) -> None:
        """Load the metadata snapshot from disk (models are not rehydrated)."""
        if self._dir is None:
            return
        path = self._dir / "records.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._log.error("No se pudo leer el registro de modelos: %r", exc)
            return
        for raw in data.get("records", []):
            record = ModelRecord.from_dict(raw)
            self._records[record.id] = record
            self._order.append(record.id)
        self._active_id = data.get("active_id")
        self._activation_stack = deque(data.get("activation_stack", []), maxlen=50)

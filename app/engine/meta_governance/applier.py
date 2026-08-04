"""Aplicador del gobierno del Meta Strategy Manager sobre el Strategy Engine.

El Meta Strategy Manager (Fase 7) calculaba pesos y activaciones, publicaba
``StrategyWeightsUpdated``/``MetaStrategyDecision``… y nadie los escuchaba salvo
el notificador de Discord. El gobierno existía sobre el papel: los pesos que
usaba el consenso seguían siendo los de configuración, fijos desde el arranque.

Este servicio cierra el lazo. Se suscribe al bus y traduce las decisiones del
MSM a llamadas de configuración sobre el ``StrategyEngine``:
``set_weight`` / ``enable_strategy`` / ``disable_strategy``.

Tres límites deliberados:

1. **Sólo configuración.** No abre ni cierra posiciones, no toca código de
   estrategias y no puede habilitar live — no conoce ni la ejecución ni el
   Live Gate. Su superficie completa son esos tres métodos.
2. **Todo auditado.** Cada cambio efectivo se registra en el audit log
   append-only con el peso anterior, el nuevo y el motivo. Un peso que cambia
   solo y sin rastro es indistinguible de un bug.
3. **Desacoplado.** Llega por eventos, igual que el resto del sistema: el MSM
   no conoce al Strategy Engine ni al revés.
"""

import logging
from typing import Any

from app.core.events.base import Event
from app.core.events.bus import EventBus, Subscription
from app.core.exceptions import ConfigurationError
from app.core.lifecycle import Service
from app.engine.strategy_engine import StrategyEngine
from app.ml.events import MetaStrategyDecision, StrategyWeightsUpdated
from app.production.audit import AuditAction, AuditLog


class MetaGovernanceApplier(Service):
    """Apply Meta Strategy Manager decisions to the Strategy Engine.

    Args:
        strategies: Strategy Engine que posee las estrategias cargadas.
        bus: Event Bus del sistema.
        audit: Audit log append-only (``None`` desactiva el registro, sólo en
            tests puros: en producción el composition root siempre lo inyecta).
        enabled: Si el gobierno se aplica de verdad. En ``False`` el servicio
            escucha y registra lo que *habría* hecho, sin tocar nada — útil
            para observar al MSM antes de dejarle gobernar.
    """

    def __init__(
        self,
        strategies: StrategyEngine,
        bus: EventBus,
        *,
        audit: AuditLog | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__("meta_governance_applier")
        self._strategies = strategies
        self._bus = bus
        self._audit = audit
        self._enabled = enabled
        self._subscriptions: list[Subscription] = []
        self._applied = 0
        self._skipped = 0
        self._log = logging.getLogger("app.engine.meta_governance")

    async def _on_start(self) -> None:
        """Subscribe to the governance events."""
        self._subscriptions.append(self._bus.subscribe(self._on_weights, StrategyWeightsUpdated))
        self._subscriptions.append(self._bus.subscribe(self._on_decision, MetaStrategyDecision))

    async def _on_stop(self) -> None:
        """Unsubscribe from the bus."""
        for subscription in self._subscriptions:
            self._bus.unsubscribe(subscription)
        self._subscriptions.clear()

    # ------------------------------------------------------------------
    # Manejadores
    # ------------------------------------------------------------------

    async def _on_weights(self, event: Event) -> None:
        """Apply a batch of dynamic weights."""
        if not isinstance(event, StrategyWeightsUpdated):
            return
        loaded = set(self._strategies.loaded)
        for name, weight in event.weights.items():
            # Una estrategia del journal que ya no está cargada (renombrada,
            # retirada) no es un error: se ignora sin ruido.
            if name not in loaded:
                self._skipped += 1
                continue
            self._apply_weight(name, float(weight), event.reason or "gobierno del MSM")

    async def _on_decision(self, event: Event) -> None:
        """Apply an activation decision on a single strategy."""
        if not isinstance(event, MetaStrategyDecision):
            return
        if event.action not in ("disable", "enable"):
            return
        if event.strategy not in set(self._strategies.loaded):
            self._skipped += 1
            return
        self._apply_activation(event.strategy, event.action == "enable", event.detail)

    # ------------------------------------------------------------------
    # Aplicación auditada
    # ------------------------------------------------------------------

    def _apply_weight(self, name: str, weight: float, reason: str) -> None:
        """Set one strategy's weight, auditing the change."""
        previous = self._strategies.weights().get(name)
        if previous is not None and abs(previous - weight) < 1e-6:
            return  # nada que cambiar: no se ensucia la auditoría
        if not self._enabled:
            self._record(
                AuditAction.STRATEGY_WEIGHT_CHANGED,
                name,
                previous,
                weight,
                {"reason": reason, "applied": False, "why": "gobierno en modo observación"},
            )
            self._skipped += 1
            return
        try:
            applied = self._strategies.set_weight(name, weight)
        except ConfigurationError:
            self._skipped += 1
            return
        self._applied += 1
        self._log.info("Peso de '%s': %s → %.4f (%s)", name, previous, applied, reason)
        self._record(
            AuditAction.STRATEGY_WEIGHT_CHANGED,
            name,
            previous,
            applied,
            {"reason": reason, "applied": True},
        )

    def _apply_activation(self, name: str, enable: bool, detail: str) -> None:
        """Enable or disable one strategy, auditing the change."""
        action = AuditAction.STRATEGY_ENABLED if enable else AuditAction.STRATEGY_DISABLED
        if not self._enabled:
            self._record(
                action,
                name,
                None,
                enable,
                {"reason": detail, "applied": False, "why": "gobierno en modo observación"},
            )
            self._skipped += 1
            return
        try:
            if enable:
                self._strategies.enable_strategy(name)
            else:
                self._strategies.disable_strategy(name)
        except ConfigurationError:
            self._skipped += 1
            return
        self._applied += 1
        self._log.info(
            "Estrategia '%s' %s (%s)", name, "activada" if enable else "desactivada", detail
        )
        self._record(action, name, not enable, enable, {"reason": detail, "applied": True})

    def _record(
        self,
        action: AuditAction,
        target: str,
        before: Any,
        after: Any,
        meta: dict[str, Any],
    ) -> None:
        """Write one audit entry (best effort: auditar nunca tumba el motor)."""
        if self._audit is None:
            return
        try:
            self._audit.record(
                action=action,
                actor="meta_strategy_manager",
                target=target,
                before=before,
                after=after,
                meta=meta,
            )
        except Exception:
            self._log.exception("No se pudo auditar el gobierno de '%s'", target)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._enabled,
            "applied": self._applied,
            "skipped": self._skipped,
            "weights": self._strategies.weights(),
        }

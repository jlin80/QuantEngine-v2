"""Explicación de una operación cerrada, en lenguaje que se pueda leer.

Responde cinco preguntas sobre una operación concreta, sin inventarse ninguna:

1. ¿qué estrategia y qué condiciones de mercado dispararon la entrada?
2. ¿qué confirmaciones pasaron y cuáles faltaron?
3. ¿por qué salió donde salió, y ese motivo le impidió llegar a su objetivo?
4. ¿qué decía la señal frente a lo que consiguió la ejecución?
5. ¿qué variables pesaron en la calidad de la señal, según el modelo activo?

**No calcula nada nuevo.** Reúne lo que ya producen el Decision Engine (las
contribuciones del consenso), el evaluador continuo (el resultado virtual por
``signal_id``, join real desde el Bloque 8), el Trade Journal (el
``context_snapshot``) y el modelo de ML activo (``explain_prediction``). Duplicar
cualquiera de esos cálculos aquí crearía un segundo número con el mismo nombre.

**El ML sigue sin decidir.** Este módulo es de lectura: no toca el Decision
Engine, no abre ni cierra nada y no puede habilitar live.

Las piezas llegan como proveedores (callables) y no como objetos del motor: es
lo que permite que el ML no dependa de ``app.engine`` ni al revés (ADR-087). El
composition root las conecta.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.models import Decision
from app.execution.models import ExitReason, TradeRecord

# Motivos de salida que **no** resuelven la tesis de la operación: la cerró algo
# externo a la señal (el régimen cambió, se acabó el tiempo, alguien intervino).
# Distinguirlos importa porque una señal con edge cortada por uno de estos es
# indistinguible, si no se separan, de una señal que nunca tuvo edge.
_THESIS_UNRESOLVED: frozenset[str] = frozenset(
    {
        ExitReason.REGIME_CHANGE.value,
        ExitReason.CONTEXT_LOST.value,
        ExitReason.TIME_EXIT.value,
        ExitReason.VOLATILITY_EXIT.value,
        ExitReason.RISK_EXIT.value,
        ExitReason.KILL_SWITCH.value,
        ExitReason.MANUAL.value,
    }
)


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalVerdict:
    """Lo que la señal consiguió por sí sola, sin ejecución de por medio."""

    signal_id: str
    strategy: str
    r_multiple: float
    outcome: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "signal_id": self.signal_id,
            "strategy": self.strategy,
            "r_multiple": self.r_multiple,
            "outcome": self.outcome,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class TradeExplanation:
    """Explicación completa de una operación, con sus huecos declarados."""

    trade_id: str
    symbol: str
    entry: dict[str, Any]
    confirmations: dict[str, Any]
    exit: dict[str, Any]
    signal_vs_execution: dict[str, Any]
    model: dict[str, Any]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "entry": self.entry,
            "confirmations": self.confirmations,
            "exit": self.exit,
            "signal_vs_execution": self.signal_vs_execution,
            "model": self.model,
            "summary": self.summary,
        }


class TradeExplainer:
    """Compose a per-trade explanation out of the evidence already recorded.

    Args:
        trades: Proveedor del historial del Trade Journal.
        decisions: Proveedor de decisiones recientes (para las contribuciones
            del consenso y el desglose de confianza).
        outcomes: Proveedor del índice ``signal_id -> resultado virtual``.
        predictor: Explicación del modelo ML activo para un contexto
            (``MLEngine.explain_prediction``). ``None`` cuando el ML está
            apagado: se reporta como ausente, no como una explicación vacía.
    """

    def __init__(
        self,
        trades: Callable[[], Sequence[TradeRecord]],
        decisions: Callable[[], Sequence[Decision]],
        outcomes: Callable[[], Mapping[str, SignalVerdict]],
        predictor: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._trades = trades
        self._decisions = decisions
        self._outcomes = outcomes
        self._predictor = predictor

    def explain(self, trade_id: str) -> TradeExplanation | None:
        """Explain one closed trade.

        Args:
            trade_id: ``trade_id`` o ``position_id`` de la operación.

        Returns:
            La explicación, o ``None`` si no existe esa operación.
        """
        trade = self._find(trade_id)
        if trade is None:
            return None
        decision = self._decision_for(trade)
        snapshot = dict(trade.context_snapshot or {})
        verdicts = self._verdicts_for(trade)
        return TradeExplanation(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            entry=self._entry(trade, decision, snapshot),
            confirmations=self._confirmations(trade, decision),
            exit=self._exit(trade, snapshot),
            signal_vs_execution=self._signal_vs_execution(trade, verdicts),
            model=self._model(trade, snapshot),
            summary=self._summary(trade, verdicts),
        )

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------

    def _find(self, trade_id: str) -> TradeRecord | None:
        """Locate a trade by ``trade_id`` (o ``position_id`` como alias)."""
        for trade in self._trades():
            if trade.trade_id == trade_id or trade.position_id == trade_id:
                return trade
        return None

    def _decision_for(self, trade: TradeRecord) -> Decision | None:
        """The decision that opened the trade, if it is still in the history."""
        if not trade.decision_id:
            return None
        for decision in self._decisions():
            if decision.decision_id == trade.decision_id:
                return decision
        return None

    def _verdicts_for(self, trade: TradeRecord) -> list[SignalVerdict]:
        """Virtual outcomes of the signals behind this trade (may be empty)."""
        index = self._outcomes()
        return [index[sid] for sid in trade.signal_ids if sid in index]

    # ------------------------------------------------------------------
    # Secciones
    # ------------------------------------------------------------------

    def _entry(
        self, trade: TradeRecord, decision: Decision | None, snapshot: Mapping[str, Any]
    ) -> dict[str, Any]:
        """What fired the entry, and under which market conditions."""
        contributions: dict[str, float] | None = None
        if decision is not None and decision.consensus is not None:
            contributions = dict(decision.consensus.contributions)
        return {
            "strategy": trade.strategy or None,
            "strategy_category": trade.strategy_category or None,
            # `None` y no 0.0: una decisión que ya no está en el historial en
            # memoria no aportó "cero", es que no se puede consultar.
            "consensus_contributions": contributions,
            "score": trade.score,
            "confidence": trade.confidence,
            "reasons": list(trade.entry_reasons),
            "conditions": {
                "regime": trade.regime or None,
                "volatility": trade.volatility or None,
                "atr": trade.atr,
                "spread_bps": trade.spread_bps,
                "sessions": snapshot.get("entry_sessions"),
            },
            "decision_available": decision is not None,
        }

    def _confirmations(self, trade: TradeRecord, decision: Decision | None) -> dict[str, Any]:
        """Which confirmations passed and which were missing."""
        if decision is None:
            return {
                "status": "no_disponible",
                "detail": (
                    "la decisión de origen ya no está en el historial en memoria; "
                    "las confirmaciones no se reconstruyen a posteriori"
                ),
            }
        return {
            "status": "disponible",
            "passed": list(decision.explanation),
            "missing": list(decision.filters_blocking),
            "agreement": decision.agreement,
            "confidence_breakdown": dict(decision.confidence_breakdown),
            "signals_considered": list(decision.signals_considered),
        }

    def _exit(self, trade: TradeRecord, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Why it closed where it closed, and whether that cut the thesis short."""
        reason = str(trade.exit_reason)
        cut_short = reason in _THESIS_UNRESOLVED
        return {
            "reason": reason,
            "reasons_detail": list(trade.exit_reasons),
            "r_multiple": trade.r_multiple,
            "pnl": trade.pnl,
            "holding_seconds": snapshot.get("holding_seconds"),
            "min_holding_seconds": snapshot.get("min_holding_seconds"),
            "entry_regime": snapshot.get("entry_regime"),
            "exit_regime": snapshot.get("exit_regime"),
            "thesis_resolved": not cut_short,
            "detail": (
                "cerrada por algo ajeno a la tesis (régimen, tiempo o intervención): "
                "no llegó a su objetivo ni a su stop"
                if cut_short
                else "la operación llegó a su objetivo o a su stop: la tesis se resolvió"
            ),
        }

    def _signal_vs_execution(
        self, trade: TradeRecord, verdicts: Sequence[SignalVerdict]
    ) -> dict[str, Any]:
        """The question the join exists to answer: señal buena, ¿ejecución mala?"""
        if not trade.signal_ids:
            return {
                "status": "unmatched_legacy",
                "detail": (
                    "la operación no lleva signal_ids (journal anterior al Bloque 8 "
                    "o posición adoptada del broker): no hay señal que comparar"
                ),
                "signal_r": None,
                "execution_r": trade.r_multiple,
                "gap_r": None,
            }
        if not verdicts:
            return {
                "status": "unmatched_unresolved",
                "detail": (
                    "las señales de origen no tienen resolución virtual: sin niveles, "
                    "o su operación virtual sigue abierta"
                ),
                "signal_r": None,
                "execution_r": trade.r_multiple,
                "gap_r": None,
            }
        # Media de las R virtuales: la decisión es multi-estrategia por diseño y
        # atribuirla a una sola sería darle a una lo que votaron varias.
        signal_r = sum(v.r_multiple for v in verdicts) / len(verdicts)
        return {
            "status": "matched",
            "detail": (
                f"la señal habría dado {signal_r:.3f}R resolviéndose sola; la ejecución "
                f"consiguió {trade.r_multiple:.3f}R"
            ),
            "signal_r": round(signal_r, 4),
            "execution_r": trade.r_multiple,
            "gap_r": round(trade.r_multiple - signal_r, 4),
            "signals": [v.to_dict() for v in verdicts],
        }

    def _model(self, trade: TradeRecord, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Per-variable contribution from the active model, if there is one."""
        if self._predictor is None:
            return {
                "status": "sin_modelo",
                "detail": "el ML está apagado o no hay modelo activo",
            }
        context: dict[str, Any] = {
            "symbol": trade.symbol,
            "regime": trade.regime,
            "volatility": trade.volatility,
            "atr": trade.atr,
            "spread_bps": trade.spread_bps,
            "score": trade.score,
            "confidence": trade.confidence,
            "strategy": trade.strategy,
            "direction": str(trade.side),
            "sessions": snapshot.get("entry_sessions"),
        }
        try:
            prediction = self._predictor(context)
        except Exception as exc:  # el explicador no puede tumbar el endpoint
            return {"status": "error", "detail": repr(exc)}
        return {"status": "disponible", "context": context, "prediction": prediction}

    def _summary(self, trade: TradeRecord, verdicts: Sequence[SignalVerdict]) -> str:
        """One-line plain-language readout of the trade."""
        strategy = trade.strategy or "estrategia no atribuida"
        regime = trade.regime or "régimen desconocido"
        line = (
            f"{strategy} abrió {trade.side} en {trade.symbol} con {regime}; "
            f"cerró por {trade.exit_reason} con {trade.r_multiple:.2f}R"
        )
        if verdicts:
            signal_r = sum(v.r_multiple for v in verdicts) / len(verdicts)
            gap = trade.r_multiple - signal_r
            if gap < -0.1:
                line += f" — la señal daba {signal_r:.2f}R, la ejecución perdió {abs(gap):.2f}R"
            elif gap > 0.1:
                line += f" — la señal daba {signal_r:.2f}R, la ejecución ganó {gap:.2f}R"
            else:
                line += f" — coincide con lo que daba la señal ({signal_r:.2f}R)"
        return line

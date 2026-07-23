"""Ingeniería de features del ML a partir del historial del propio motor.

Regla de oro de la fase: **no** se usa sólo el precio. Las features describen el
*contexto de la decisión* (hora, sesión, régimen, volatilidad, ATR, spread,
score, confianza, R:R planificado, nº de confirmaciones, dirección) — todo
conocido en el momento de abrir. El resultado (PnL, R, motivo de salida) es la
**etiqueta**, nunca una feature: así el modelo aprende a filtrar operaciones
malas sin mirar el futuro.

El esquema es fijo y versionado para que entrenamiento e inferencia produzcan
exactamente el mismo vector.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.execution.models.enums import PositionSide
from app.execution.models.trades import TradeRecord

FEATURE_SCHEMA_VERSION = "1.0"

_REGIME_VOCAB: tuple[str, ...] = (
    "trending",
    "ranging",
    "expansion",
    "compression",
    "breakout",
    "reversal",
    "high_volatility",
    "low_volatility",
    "unknown",
)
_VOL_VOCAB: tuple[str, ...] = ("low", "normal", "high")
_SESSIONS: tuple[str, ...] = ("asia", "europe", "america", "off")

_NUMERIC_NAMES: tuple[str, ...] = (
    "hour_norm",
    "score",
    "confidence",
    "atr_pct",
    "spread_bps",
    "planned_rr",
    "n_confirmations",
    "side_long",
)


def _sessions_for_hour(hour: int) -> dict[str, float]:
    """Session membership flags for a UTC hour (overlaps allowed)."""
    asia = 1.0 if 0 <= hour < 9 else 0.0
    europe = 1.0 if 7 <= hour < 16 else 0.0
    america = 1.0 if 13 <= hour < 22 else 0.0
    off = 1.0 if asia == europe == america == 0.0 else 0.0
    return {"asia": asia, "europe": europe, "america": america, "off": off}


def _one_hot(value: str, vocab: tuple[str, ...], *, default: str) -> dict[str, float]:
    """One-hot encode a categorical value against a fixed vocabulary."""
    key = value if value in vocab else default
    return {name: (1.0 if name == key else 0.0) for name in vocab}


class FeatureEngineer:
    """Deterministic, versioned feature builder for trade-quality models.

    El mismo esquema se usa para construir el dataset de entrenamiento (desde
    ``TradeRecord``) y para inferir sobre una operación candidata (desde el
    contexto de una decisión). Es stateless: no aprende nada, sólo transforma.
    """

    @property
    def version(self) -> str:
        """Feature schema version."""
        return FEATURE_SCHEMA_VERSION

    def feature_names(self) -> list[str]:
        """Ordered feature names produced by this engineer."""
        names = list(_NUMERIC_NAMES)
        names += [f"sess_{s}" for s in _SESSIONS]
        names += [f"regime_{r}" for r in _REGIME_VOCAB]
        names += [f"vol_{v}" for v in _VOL_VOCAB]
        return names

    @property
    def n_features(self) -> int:
        """Length of the feature vector."""
        return len(self.feature_names())

    # ------------------------------------------------------------------
    # Construcción del vector
    # ------------------------------------------------------------------

    def _vector(
        self,
        *,
        hour: int,
        score: float,
        confidence: float,
        atr_pct: float,
        spread_bps: float,
        planned_rr: float,
        n_confirmations: int,
        side_long: float,
        regime: str,
        volatility: str,
    ) -> list[float]:
        """Assemble the fixed-length feature vector from raw context."""
        numeric = {
            "hour_norm": hour / 23.0,
            "score": score / 100.0,
            "confidence": confidence,
            "atr_pct": atr_pct,
            "spread_bps": spread_bps,
            "planned_rr": planned_rr,
            "n_confirmations": n_confirmations / 5.0,
            "side_long": side_long,
        }
        sessions = _sessions_for_hour(hour)
        regimes = _one_hot(regime, _REGIME_VOCAB, default="unknown")
        vols = _one_hot(volatility, _VOL_VOCAB, default="normal")
        vector = [numeric[name] for name in _NUMERIC_NAMES]
        vector += [sessions[s] for s in _SESSIONS]
        vector += [regimes[r] for r in _REGIME_VOCAB]
        vector += [vols[v] for v in _VOL_VOCAB]
        return vector

    def from_trade(self, trade: TradeRecord) -> list[float]:
        """Build the feature vector from a closed trade's entry context."""
        atr_pct = 0.0
        if trade.atr is not None and trade.entry_price > 0:
            atr_pct = trade.atr / trade.entry_price * 100.0
        return self._vector(
            hour=trade.entry_time.hour,
            score=trade.score,
            confidence=trade.confidence,
            atr_pct=atr_pct,
            spread_bps=trade.spread_bps,
            planned_rr=_planned_rr(trade.entry_price, trade.stop_loss, trade.take_profit),
            n_confirmations=len(trade.entry_reasons),
            side_long=1.0 if trade.side is PositionSide.LONG else 0.0,
            regime=trade.regime,
            volatility=trade.volatility,
        )

    def from_context(self, context: Mapping[str, Any]) -> list[float]:
        """Build the feature vector from a candidate-trade context mapping.

        Se usa en inferencia (antes de abrir). Claves reconocidas: ``hour`` o
        ``entry_time``, ``score``, ``confidence``, ``atr``, ``atr_pct``,
        ``entry_price``, ``spread_bps``, ``side``, ``regime``, ``volatility``,
        ``stop_loss``, ``take_profit``, ``entry_reasons`` o ``n_confirmations``.
        """
        hour = _hour_of(context)
        entry_price = float(context.get("entry_price", 0.0) or 0.0)
        atr_pct = float(context.get("atr_pct", 0.0) or 0.0)
        atr = context.get("atr")
        if atr_pct == 0.0 and atr is not None and entry_price > 0:
            atr_pct = float(atr) / entry_price * 100.0
        reasons = context.get("entry_reasons")
        n_conf = (
            len(reasons)
            if isinstance(reasons, list | tuple)
            else int(context.get("n_confirmations", 0) or 0)
        )
        return self._vector(
            hour=hour,
            score=float(context.get("score", 0.0) or 0.0),
            confidence=float(context.get("confidence", 0.0) or 0.0),
            atr_pct=atr_pct,
            spread_bps=float(context.get("spread_bps", 0.0) or 0.0),
            planned_rr=_planned_rr(
                entry_price, context.get("stop_loss"), context.get("take_profit")
            ),
            n_confirmations=n_conf,
            side_long=_side_long(context.get("side")),
            regime=str(context.get("regime", "unknown")),
            volatility=str(context.get("volatility", "normal")),
        )

    @staticmethod
    def label(trade: TradeRecord, *, kind: str = "win") -> int:
        """Binary training label from a trade outcome.

        Args:
            trade: Operación cerrada.
            kind: ``win`` (PnL > 0), ``rr_positive`` (R ≥ 0) o ``not_stopped``
                (no salió por stop).

        Returns:
            1 (buena operación) o 0 (mala operación).
        """
        if kind == "rr_positive":
            return 1 if trade.r_multiple >= 0 else 0
        if kind == "not_stopped":
            return 1 if trade.exit_reason.value != "stop_loss" else 0
        return 1 if trade.is_win else 0


def _planned_rr(entry: float, stop: float | None, target: float | None) -> float:
    """Planned reward-to-risk from entry/stop/target (0 when undefined)."""
    if stop is None or target is None or entry <= 0:
        return 0.0
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return reward / risk if risk > 0 else 0.0


def _hour_of(context: Mapping[str, Any]) -> int:
    """Resolve the UTC hour from a context mapping."""
    if "hour" in context and context["hour"] is not None:
        return int(context["hour"]) % 24
    moment = context.get("entry_time")
    if isinstance(moment, datetime):
        return moment.hour
    return 0


def _side_long(side: Any) -> float:
    """Map a side value (enum or string) to the ``side_long`` flag."""
    if isinstance(side, PositionSide):
        return 1.0 if side is PositionSide.LONG else 0.0
    return 1.0 if str(side).lower() in {"long", "buy"} else 0.0

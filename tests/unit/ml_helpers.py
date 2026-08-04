"""Fábricas de datos para las pruebas del Machine Learning (Fase 7).

El ML se entrena con el historial del propio motor (``TradeRecord``). Estas
utilidades fabrican operaciones cerradas deterministas y un conjunto *aprendible*
(el score/confianza/RR separan claramente ganadoras de perdedoras) para que los
modelos superen de forma estable la puerta de validación en las pruebas.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.execution.models.enums import ExitReason, PositionSide
from app.execution.models.trades import TradeRecord
from app.ml.evaluation.metrics import ClassificationMetrics
from app.ml.evaluation.validation import CrossValidationResult
from app.ml.interfaces.model import Model
from app.ml.training.trainer import TrainingResult

# Posterior a todos los fixes de ejecución conocidos: estas operaciones de
# prueba pertenecen a la era limpia, así que el saneamiento del training set
# (`app.ml.datasets.eras`) no las excluye. Los tests que prueban el saneamiento
# en sí fabrican sus propias fechas a propósito.
_BASE = datetime(2026, 8, 1, tzinfo=UTC)


def make_trade(*, strategy: str | None = None, **overrides: Any) -> TradeRecord:
    """Build a closed :class:`TradeRecord` with sensible defaults.

    Pass ``strategy=`` to attribute the trade to its originating strategy
    (used by the strategy-intelligence ranking). Se rellena el campo de primera
    clase ``strategy``, que es lo que hace la ejecución real desde la
    atribución de la decisión.
    """
    entry = overrides.pop("entry_time", _BASE)
    fields: dict[str, Any] = {
        "position_id": "pos",
        "symbol": "BTCUSDT",
        "side": PositionSide.LONG,
        "quantity": 1.0,
        "entry_time": entry,
        "exit_time": overrides.pop("exit_time", entry + timedelta(minutes=45)),
        "entry_price": 100.0,
        "exit_price": 102.0,
        "stop_loss": 98.0,
        "take_profit": 106.0,
        "commission": 0.2,
        "spread_bps": 2.0,
        "pnl": 1.0,
        "pnl_gross": 1.2,
        "r_multiple": 1.0,
        "atr": 1.5,
        "volatility": "normal",
        "regime": "trending",
        "score": 50.0,
        "confidence": 0.5,
        "exit_reason": ExitReason.TAKE_PROFIT,
        "entry_reasons": ("trend", "momentum"),
    }
    if strategy is not None:
        fields["strategy"] = strategy
    fields.update(overrides)
    return TradeRecord(**fields)


def _winner(entry: datetime, i: int) -> TradeRecord:
    """A high-quality winning trade (strong score/confidence/RR signal)."""
    return make_trade(
        entry_time=entry,
        exit_time=entry + timedelta(minutes=45),
        score=72.0 + (i % 18),
        confidence=0.72 + 0.01 * (i % 15),
        stop_loss=98.0,
        take_profit=106.0,  # RR ~3
        exit_price=106.0,
        pnl=2.0,
        pnl_gross=2.2,
        r_multiple=2.0,
        regime="trending",
        volatility="normal",
        exit_reason=ExitReason.TAKE_PROFIT,
        entry_reasons=("trend", "momentum", "breakout"),
    )


def _loser(entry: datetime, i: int) -> TradeRecord:
    """A low-quality losing trade (weak score/confidence/RR signal)."""
    return make_trade(
        entry_time=entry,
        exit_time=entry + timedelta(minutes=45),
        score=20.0 + (i % 15),
        confidence=0.20 + 0.01 * (i % 12),
        stop_loss=97.0,
        take_profit=101.0,  # RR ~0.3
        exit_price=97.0,
        pnl=-1.0,
        pnl_gross=-0.9,
        r_multiple=-1.0,
        regime="ranging",
        volatility="high",
        exit_reason=ExitReason.STOP_LOSS,
        entry_reasons=("counter",),
    )


def learnable_trades(n: int = 80) -> list[TradeRecord]:
    """Interleaved winners/losers with a clean, learnable signal.

    Classes are spread across the whole timeline so every temporal train/CV
    split contains both (winners and losers alternate in exit-time order).
    """
    trades: list[TradeRecord] = []
    for i in range(n):
        entry = _BASE + timedelta(minutes=30 * i)
        trades.append(_winner(entry, i) if i % 2 == 0 else _loser(entry, i))
    return trades


def strategy_trades(
    name: str,
    n: int,
    *,
    win_rate: float = 0.6,
    r_win: float = 2.0,
    r_loss: float = -1.0,
    regime: str = "trending",
    symbol: str = "BTCUSDT",
) -> list[TradeRecord]:
    """Trades for one strategy with a target win rate (for ranking/governance)."""
    trades: list[TradeRecord] = []
    for i in range(n):
        entry = _BASE + timedelta(hours=i)
        win = (i % 10) < round(win_rate * 10)
        trades.append(
            make_trade(
                strategy=name,
                symbol=symbol,
                entry_time=entry,
                exit_time=entry + timedelta(minutes=30),
                regime=regime,
                score=65.0 if win else 40.0,
                confidence=0.65 if win else 0.4,
                pnl=r_win if win else r_loss,
                pnl_gross=r_win + 0.2 if win else r_loss + 0.1,
                r_multiple=r_win if win else r_loss,
                exit_price=104.0 if win else 98.0,
                exit_reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
            )
        )
    return trades


def make_metrics(auc: float, *, accuracy: float = 0.70, samples: int = 80) -> ClassificationMetrics:
    """A :class:`ClassificationMetrics` bundle with the given headline values."""
    positives = samples // 2
    return ClassificationMetrics(
        samples=samples,
        positives=positives,
        accuracy=accuracy,
        precision=accuracy,
        recall=accuracy,
        f1=accuracy,
        auc=auc,
        log_loss=0.5,
        tp=positives,
        tn=samples - positives,
        fp=0,
        fn=0,
    )


def make_training_result(
    model: Model,
    *,
    auc: float,
    accuracy: float = 0.70,
    samples: int = 80,
    cv_auc: float | None = None,
) -> TrainingResult:
    """Assemble a :class:`TrainingResult` with controlled metrics for gate tests.

    ``cv_auc`` permite disociar la validación cruzada del holdout para probar la
    puerta anti-sobreajuste; por defecto coinciden.
    """
    holdout = make_metrics(auc, accuracy=accuracy, samples=samples)
    cv = CrossValidationResult(
        folds=3,
        mean_auc=auc if cv_auc is None else cv_auc,
        mean_accuracy=accuracy,
        mean_f1=accuracy,
    )
    return TrainingResult(
        model=model,
        model_type=model.model_type.value,
        params=model.params(),
        holdout=holdout,
        cross_validation=cv,
        n_train=samples,
        n_test=samples // 4,
        feature_names=list(getattr(model, "feature_names", []) or []),
        dataset_summary={"samples": samples, "positives": samples // 2},
    )


def context(**overrides: Any) -> Mapping[str, Any]:
    """A candidate-trade context mapping for inference/explainability tests."""
    ctx: dict[str, Any] = {
        "hour": 14,
        "score": 78.0,
        "confidence": 0.8,
        "atr": 1.5,
        "entry_price": 100.0,
        "spread_bps": 2.0,
        "side": "long",
        "regime": "trending",
        "volatility": "normal",
        "stop_loss": 98.0,
        "take_profit": 106.0,
        "entry_reasons": ["trend", "momentum", "breakout"],
    }
    ctx.update(overrides)
    return ctx

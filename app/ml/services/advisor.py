"""AI Advisor: asistente interno explicable (Fase 7).

Responde en lenguaje llano preguntas del operador —qué estrategia rinde mejor,
qué activo, qué horarios, qué cambió esta semana, cuáles son las peores
estrategias, qué parámetros revisar, por qué se abriría o rechazaría una
operación— siempre respaldado por datos. Nunca decide ni opera: asesora.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.execution.models.trades import TradeRecord
from app.ml.optimization.parameters import ParameterRecommender
from app.ml.services.risk_advisor import RiskAdvisor
from app.ml.services.stats import TradeStats, group_stats
from app.ml.services.strategy_intelligence import StrategyScore


class AIAdvisor:
    """Explainable question-answering over the engine's own history.

    Args:
        risk_advisor: Asesor de riesgo por ML.
        recommender: Recomendador de parámetros.
    """

    def __init__(self, risk_advisor: RiskAdvisor, recommender: ParameterRecommender) -> None:
        self._risk = risk_advisor
        self._recommender = recommender

    def best_strategy(self, scores: Sequence[StrategyScore]) -> dict[str, Any]:
        """Which strategy performs best right now."""
        if not scores:
            return _answer("¿Qué estrategia funciona mejor?", "Aún no hay datos suficientes.", {})
        best = scores[0]
        return _answer(
            "¿Qué estrategia funciona mejor?",
            f"'{best.name}' es la mejor (score {best.score:.0f}, expectativa "
            f"{best.historical.expectancy_r:+.2f}R en {best.trades} operaciones).",
            {"strategy": best.to_dict()},
        )

    def worst_strategies(self, scores: Sequence[StrategyScore], *, k: int = 3) -> dict[str, Any]:
        """Which strategies perform worst."""
        worst = sorted(scores, key=lambda s: s.score)[:k]
        names = ", ".join(f"{s.name} ({s.score:.0f})" for s in worst) or "—"
        return _answer(
            "¿Cuáles son las peores estrategias?",
            f"Las de menor score: {names}.",
            {"strategies": [s.to_dict() for s in worst]},
        )

    def best_asset(self, trades: Sequence[TradeRecord], *, min_trades: int = 10) -> dict[str, Any]:
        """Which instrument performs best."""
        groups = group_stats(trades, "symbol")
        ranked = _rank_groups(groups, min_trades)
        if not ranked:
            return _answer("¿Qué activo tiene mejor rendimiento?", "Datos insuficientes.", groups)
        name, stats = ranked[0]
        return _answer(
            "¿Qué activo tiene mejor rendimiento?",
            f"'{name}' con expectativa {stats['expectancy_r']:+.2f}R "
            f"({stats['trades']} operaciones).",
            {"by_asset": groups},
        )

    def best_hours(self, trades: Sequence[TradeRecord]) -> dict[str, Any]:
        """Which sessions/hours perform best."""
        by_session = group_stats(trades, "session")
        ranked = _rank_groups(by_session, 1)
        best = ranked[0][0] if ranked else "—"
        return _answer(
            "¿Cuáles son los mejores horarios?",
            f"La mejor sesión es '{best}'.",
            {"by_session": by_session, "by_hour": group_stats(trades, "hour")},
        )

    def what_changed(
        self, recent: Sequence[TradeRecord], previous: Sequence[TradeRecord]
    ) -> dict[str, Any]:
        """What changed between two windows of trades."""
        now = TradeStats.from_trades(recent)
        before = TradeStats.from_trades(previous)
        d_exp = now.expectancy_r - before.expectancy_r
        d_wr = now.win_rate - before.win_rate
        verdict = "mejoró" if d_exp > 0.05 else "empeoró" if d_exp < -0.05 else "se mantiene"
        return _answer(
            "¿Qué cambió esta semana?",
            f"El rendimiento {verdict}: expectativa {d_exp:+.2f}R, win rate {d_wr:+.0%}.",
            {"recent": now.to_dict(), "previous": before.to_dict()},
        )

    def why_trade(self, trade: TradeRecord) -> dict[str, Any]:
        """Why a given trade was opened (from its recorded reasons)."""
        reasons = list(trade.entry_reasons) or ["sin razones registradas"]
        outcome = "ganadora" if trade.is_win else "perdedora"
        return _answer(
            f"¿Por qué se abrió la operación {trade.trade_id[:8]}?",
            f"Score {trade.score:.0f}, confianza {trade.confidence:.0%}, régimen "
            f"'{trade.regime}'. Razones: {', '.join(reasons)}. Resultado: {outcome} "
            f"({trade.r_multiple:+.2f}R).",
            {"trade": trade.to_dict()},
        )

    def recommend_parameters(self, scores: Sequence[StrategyScore]) -> dict[str, Any]:
        """Which parameters to review, per strategy."""
        recs = self._recommender.recommend_all(scores)
        total = sum(len(v) for v in recs.values())
        return _answer(
            "¿Qué parámetros recomienda modificar?",
            (
                f"{total} recomendaciones en {len(recs)} estrategias."
                if total
                else "Sin cambios sugeridos."
            ),
            {"recommendations": recs},
        )

    def assess_candidate(
        self, context: Mapping[str, Any], similar: Sequence[TradeRecord]
    ) -> dict[str, Any]:
        """Whether to open a candidate trade (advisory), with the reasons why."""
        assessment = self._risk.assess(context, similar)
        return _answer(
            "¿Debería abrir esta operación?",
            f"Recomendación: {assessment.recommendation} (P éxito {assessment.p_success:.0%}).",
            {"assessment": assessment.to_dict()},
        )


def _answer(question: str, answer: str, data: dict[str, Any]) -> dict[str, Any]:
    """Package a question, an explainable answer and its supporting data."""
    return {"question": question, "answer": answer, "data": data}


def _rank_groups(
    groups: dict[str, dict[str, Any]], min_trades: int
) -> list[tuple[str, dict[str, Any]]]:
    """Rank groups by expectancy among those with enough trades."""
    eligible = [(name, s) for name, s in groups.items() if s.get("trades", 0) >= min_trades]
    eligible.sort(key=lambda kv: kv[1].get("expectancy_r", 0.0), reverse=True)
    return eligible

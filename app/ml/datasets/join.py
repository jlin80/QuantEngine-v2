"""Unión fila a fila entre el Trade Journal y el evaluador continuo.

El Bloque 4 dejó escrita la limitación: ``signal_quality`` se aproximaba
filtrando el journal **por motivo de salida**, porque el resultado virtual de
cada señal no se persistía y el ``TradeRecord`` no llevaba los ``signal_id``.
Esa aproximación es honesta y no tiene lookahead, pero sigue midiendo
operaciones *ejecutadas*: no separa "la señal no tenía edge" de "la señal lo
tenía y la ejecución no lo capturó".

Con ambos huecos cerrados (Bloque 8), este módulo hace el join real: para cada
operación, el resultado virtual de las señales que la originaron.

**Los casos sin match no se ocultan, se cuentan.** Son tres y significan cosas
distintas:

``unmatched_legacy``
    La operación no lleva ``signal_ids``. Es el journal anterior al Bloque 8 y
    las posiciones adoptadas del broker al arrancar. Caen al camino antiguo
    (aproximación por motivo de salida), no se descartan: son la mayor parte
    del historial acumulado y tirarlas dejaría al ML sin datos.

``unmatched_unresolved``
    Lleva ``signal_ids`` pero el evaluador no tiene resolución para ninguno: la
    señal no traía niveles, o su operación virtual sigue abierta. Se excluye de
    la etiqueta de señal e **incluye** en las de ejecución — la ejecución sí
    ocurrió y es medible.

``signal_without_trade``
    Señal con resultado virtual y sin operación ninguna: no pasó los filtros o
    el riesgo la vetó. No es una fila del dataset de ejecución, pero es la
    evidencia más limpia que existe sobre la calidad de esa señal, y por eso se
    cuenta aparte en vez de desaparecer.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.execution.models.trades import TradeRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalOutcome:
    """Resolución virtual de una señal, vista desde la capa de ML.

    Se declara aquí, y no se importa de ``app.engine.evaluation``, por la misma
    razón que :class:`~app.ml.services.strategy_intelligence.VirtualStrategyStats`
    (ADR-087): el ML no debe depender del motor de estrategias. El composition
    root adapta el ``VirtualOutcomeStore`` a esta forma.

    Attributes:
        signal_id: Señal de origen (clave del join).
        strategy: Estrategia que la emitió.
        r_multiple: Resultado virtual en múltiplos de R.
        outcome: ``win`` / ``loss`` / ``timeout``.
    """

    signal_id: str
    strategy: str
    r_multiple: float
    outcome: str


MATCHED = "matched"
UNMATCHED_LEGACY = "unmatched_legacy"
UNMATCHED_UNRESOLVED = "unmatched_unresolved"


@dataclass(frozen=True, kw_only=True, slots=True)
class JoinedTrade:
    """Una operación ejecutada junto a su resolución virtual, si la hay.

    Attributes:
        trade: La operación cerrada del Trade Journal.
        outcomes: Resultados virtuales de sus señales, en el orden en que la
            decisión las consideró.
        status: ``matched`` / ``unmatched_legacy`` / ``unmatched_unresolved``.
    """

    trade: TradeRecord
    outcomes: tuple[SignalOutcome, ...]
    status: str

    @property
    def matched(self) -> bool:
        """Whether the trade has at least one virtual resolution."""
        return bool(self.outcomes)

    @property
    def signal_r(self) -> float | None:
        """R virtual de la operación, o ``None`` si no hay match.

        Con varias señales se toma la **media**: la decisión es multi-estrategia
        por diseño y quedarse con una sola sería atribuir a una lo que votaron
        varias. La media es la lectura neutra, y el desglose sigue disponible en
        :attr:`outcomes` para quien quiera otra.
        """
        if not self.outcomes:
            return None
        return sum(o.r_multiple for o in self.outcomes) / len(self.outcomes)

    @property
    def signal_had_edge(self) -> bool | None:
        """Si la señal resolvió a favor, según el evaluador (``None`` sin match).

        Esta es la pregunta que la aproximación por motivo de salida no podía
        responder: la juzga el evaluador continuo sobre precio posterior a la
        señal, **con independencia de lo que la ejecución hiciera después**.
        """
        r = self.signal_r
        return None if r is None else r >= 0


@dataclass(frozen=True, kw_only=True, slots=True)
class JoinResult:
    """Resultado completo del join, con su procedencia auditable.

    Attributes:
        rows: Una entrada por operación del journal, en el orden recibido.
        signal_without_trade: Señales resueltas que nunca produjeron operación.
    """

    rows: tuple[JoinedTrade, ...]
    signal_without_trade: tuple[SignalOutcome, ...]

    def breakdown(self) -> dict[str, Any]:
        """Recuento por estado, para ``metadata["join_breakdown"]``.

        Returns:
            Números de cada caso, más el detalle por estrategia de las señales
            que nunca llegaron a operarse.
        """
        counts = {MATCHED: 0, UNMATCHED_LEGACY: 0, UNMATCHED_UNRESOLVED: 0}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        orphans: dict[str, int] = {}
        for outcome in self.signal_without_trade:
            orphans[outcome.strategy] = orphans.get(outcome.strategy, 0) + 1
        return {
            "trades": len(self.rows),
            **counts,
            "signal_without_trade": len(self.signal_without_trade),
            "signal_without_trade_by_strategy": dict(sorted(orphans.items())),
        }

    def matched_rows(self) -> tuple[JoinedTrade, ...]:
        """Only the rows that carry a virtual resolution."""
        return tuple(row for row in self.rows if row.matched)


def join_trades_with_outcomes(
    trades: Sequence[TradeRecord],
    outcomes: Mapping[str, SignalOutcome] | Iterable[SignalOutcome],
) -> JoinResult:
    """Join executed trades with the virtual outcome of their signals.

    Args:
        trades: Operaciones cerradas del Trade Journal.
        outcomes: Índice ``signal_id`` → resultado, o un iterable de resultados.

    Returns:
        El join completo, con el desglose de los casos sin match.
    """
    index: Mapping[str, SignalOutcome]
    if isinstance(outcomes, Mapping):
        index = outcomes
    else:
        built: dict[str, SignalOutcome] = {}
        for outcome in outcomes:
            built.setdefault(outcome.signal_id, outcome)
        index = built

    rows: list[JoinedTrade] = []
    consumed: set[str] = set()
    for trade in trades:
        if not trade.signal_ids:
            rows.append(JoinedTrade(trade=trade, outcomes=(), status=UNMATCHED_LEGACY))
            continue
        found = tuple(index[sid] for sid in trade.signal_ids if sid in index)
        consumed.update(o.signal_id for o in found)
        rows.append(
            JoinedTrade(
                trade=trade,
                outcomes=found,
                status=MATCHED if found else UNMATCHED_UNRESOLVED,
            )
        )

    orphans = tuple(o for sid, o in index.items() if sid not in consumed)
    return JoinResult(rows=tuple(rows), signal_without_trade=orphans)

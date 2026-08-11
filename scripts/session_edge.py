"""Edge por (estrategia, sesión) en XAUUSD, con corrección estadística.

Corre **cada estrategia sola** (las demás desactivadas) sobre histórico 1m real
del terminal MT5, atribuye cada operación y cada señal a la sesión activa **en
el momento de la entrada**, y aplica el kill criteria fijado en
``docs/session_edge.md`` antes de correr nada: umbral de muestra, bootstrap,
Benjamini-Hochberg y estabilidad entre sub-periodos.

Dos poblaciones, contadas por separado y nunca mezcladas:

- **señales**: todas las que emitió la estrategia, resueltas por el evaluador
  continuo real (TP/SL/timeout puros, sin ejecución de por medio);
- **operaciones**: las que sobrevivieron a filtros, riesgo y sizing y llegaron
  al Execution Engine.

Uso:
    python -m scripts.session_edge XAUUSDM --bars 50000 --spread-bps 0.6

No opera, no envía órdenes y no habilita live: sólo lee histórico y simula.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtesting.api import BacktestLab
from app.backtesting.mt5_history import pull_candles
from app.backtesting.quant_source import QuantCoreDecisionSource
from app.backtesting.session_edge import (
    CellResult,
    CellSample,
    evaluate_cells,
    session_cell,
    verdict_summary,
)
from app.config.settings import QuantStrategySettings, Settings, get_settings
from app.engine.evaluation import PerformanceTracker
from app.engine.evaluation.outcomes import VirtualOutcome, VirtualOutcomeStore
from app.engine.models import SignalRecord
from app.engine.plugins import PluginLoader
from app.market.models import Candle

# Bloque 2 del enunciado: estas estrategias dependen de order flow que MT5 no
# publica (sin ORDERBOOK en _CAPABILITIES y sin eventos Trade del polling).
# Confirmado tres veces, la última con 0 operaciones en BTC y ETH. Medirlas por
# sesión sería medir ruido puro, así que se excluyen del análisis y se listan
# aparte. **No se desactivan aquí**: eso es una decisión del operador.
ORDER_FLOW_EXCLUDED = ("cvd", "delta_confirmation", "orderbook_imbalance")


class _CollectingStore(VirtualOutcomeStore):
    """Store del evaluador que acumula en memoria en vez de escribir al JSONL.

    El fichero de producción es append-only y compartido; un barrido de 20
    estrategias no tiene por qué contaminarlo con resultados de laboratorio.
    """

    def __init__(self) -> None:
        super().__init__(None, persist=False)
        self.rows: list[VirtualOutcome] = []

    def record(self, outcome: VirtualOutcome) -> None:
        """Keep the outcome in memory (y en el store base, que no persiste)."""
        self.rows.append(outcome)
        super().record(outcome)


def pull_mt5_candles(symbol: str, bars: int) -> list[Candle]:
    """Fetch ``bars`` closed 1m candles for ``symbol`` from the MT5 terminal."""
    import MetaTrader5 as mt5  # noqa: N813  # sólo existe en Windows con terminal

    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() falló: {mt5.last_error()}")
    try:

        def _resolve(sym: str) -> str:
            for cand in (sym, sym.lower(), sym[:-1] + sym[-1].lower()):
                if mt5.symbol_info(cand) is not None:
                    return cand
            return sym

        return pull_candles(mt5, symbol, bars, resolve=_resolve)
    finally:
        mt5.shutdown()


def isolate(
    base: Settings, keep: str, names: Sequence[str], *, lift_loss_streak_halt: bool = False
) -> Settings:
    """Copy of settings with only ``keep`` enabled among ``names``.

    Args:
        base: Configuración de partida.
        keep: Única estrategia que queda habilitada.
        names: Catálogo completo (las demás se desactivan).
        lift_loss_streak_halt: Pone ``max_consecutive_losses`` a 0 (su valor
            «apagado», que sí está bien guardado). **Sólo para laboratorio.**
            Con el valor de producción (5) el Risk Manager entra en un estado
            absorbente: al llegar a 5 pérdidas seguidas bloquea toda entrada, y
            como el contador sólo se reinicia con una operación **ganadora**, ya
            no puede volver a abrir nunca. Medido: 601 decisiones aceptadas y 8
            operaciones, todas del primer día. Sin levantarlo no hay muestra que
            analizar, y lo que se estaría midiendo es el freno, no el edge.
    """
    trial = base.model_copy(deep=True)
    for other in names:
        cfg = trial.quant.strategies.get(other) or QuantStrategySettings()
        trial.quant.strategies[other] = cfg.model_copy(update={"enabled": other == keep})
    trial.quant.enabled = True
    if lift_loss_streak_halt:
        trial.execution.risk = trial.execution.risk.model_copy(update={"max_consecutive_losses": 0})
    return trial


class _LiveMarketProxy:
    """Reenvía las lecturas al mercado **actual** del source.

    ``QuantCoreDecisionSource.reset()`` sustituye su ``MarketDataService`` al
    empezar cada corrida. Un tracker que guardara la referencia de antes
    resolvería contra un mercado vacío y reportaría ceros — que es justo el
    error de "la ausencia de medición no es un cero".
    """

    def __init__(self, source: QuantCoreDecisionSource) -> None:
        self._source = source

    def __getattr__(self, name: str) -> object:
        return getattr(self._source.market, name)


def run_strategy(
    settings: Settings, symbol: str, candles: Sequence[Candle], *, spread_bps: float, balance: float
) -> tuple[list[tuple[str, float, datetime]], list[tuple[str, float]]]:
    """Run one isolated strategy and return its per-session trades and signals.

    Returns:
        ``(trades, signals)`` donde cada operación es ``(celda, R, entrada)`` y
        cada señal resuelta es ``(celda, R)``.
    """
    lab = BacktestLab(settings)
    outcomes = _CollectingStore()
    holder: list[PerformanceTracker] = []

    def _sink(record: SignalRecord) -> None:
        holder[0].on_signal_record(record)

    source = QuantCoreDecisionSource(
        settings,
        symbol,
        spread_bps=spread_bps,
        signal_sink=_sink,
        on_bar_end=lambda candle: holder[0].evaluate_open(now=candle.end),
    )
    tracker = PerformanceTracker(
        settings.quant.evaluation,
        _LiveMarketProxy(source),  # type: ignore[arg-type]
        outcomes,
    )
    holder.append(tracker)

    config = lab.make_config(
        symbol,
        timeframe="1m",
        label=f"{symbol.lower()}-session-edge",
        spread_bps=spread_bps,
        initial_balance=balance,
    )
    result = lab.run_backtest(candles, source, config)

    trades: list[tuple[str, float, datetime]] = []
    for trade in result.trades:
        snapshot = trade.context_snapshot or {}
        cell = session_cell(snapshot.get("entry_sessions"))
        trades.append((cell, trade.r_multiple, trade.entry_time))

    signals = [
        (session_cell(_sessions_at(settings, row.opened_at)), row.r_multiple)
        for row in outcomes.rows
    ]
    return trades, signals


def _sessions_at(settings: Settings, moment: datetime) -> tuple[str, ...]:
    """Sesiones activas a una hora UTC, con la misma tabla que el motor."""
    hour = moment.hour
    return tuple(
        name
        for name, (start, end) in settings.quant.context.session_hours.items()
        if (start <= hour < end) or (start > end and (hour >= start or hour < end))
    )


def build_samples(
    per_strategy: dict[str, tuple[list[tuple[str, float, datetime]], list[tuple[str, float]]]],
) -> list[CellSample]:
    """Fold the per-strategy runs into one sample per (strategy, session) cell."""
    samples: dict[tuple[str, str], CellSample] = {}

    def _cell(strategy: str, session: str) -> CellSample:
        key = (strategy, session)
        if key not in samples:
            samples[key] = CellSample(strategy=strategy, session=session)
        return samples[key]

    for strategy, (trades, signals) in per_strategy.items():
        for session, r_value, moment in trades:
            sample = _cell(strategy, session)
            sample.trade_r.append(r_value)
            sample.trade_times.append(moment)
        for session, r_value in signals:
            _cell(strategy, session).signal_r.append(r_value)
    return sorted(samples.values(), key=lambda s: (s.strategy, s.session))


def subperiod_boundaries(candles: Sequence[Candle], parts: int) -> list[datetime]:
    """Internal boundaries splitting the candle range into ``parts`` equal slices.

    Se calculan **por calendario y antes de mirar resultados**: partir por número
    de operaciones dejaría que el propio resultado eligiera dónde cortar.
    """
    start, end = candles[0].start, candles[-1].end
    span = (end - start) / parts
    return [start + span * i for i in range(1, parts)]


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def print_table(results: Sequence[CellResult]) -> None:
    """Print the deliverable table: every cell, in all four categories."""
    order = {"edge_estable": 0, "inestable": 1, "sin_edge": 2, "muestra_insuficiente": 3}
    rows = sorted(results, key=lambda r: (order[r.verdict], -(r.expectancy_r or -99)))
    header = (
        f"{'estrategia':24s} {'sesión':16s} {'n_señ':>6s} {'n_ops':>6s} "
        f"{'exp R':>8s} {'PF':>7s} {'IC95 inf':>9s} {'p':>7s} {'veredicto':<20s} acción"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        action = {
            "edge_estable": "candidata a peso por sesión",
            "inestable": "no cablear: posible ruido",
            "sin_edge": "no cablear",
            "muestra_insuficiente": "más datos antes de concluir",
        }[r.verdict]
        pf = "inf" if r.profit_factor == float("inf") else _fmt(r.profit_factor, 2)
        print(
            f"{r.strategy:24s} {r.session:16s} {r.n_signals:6d} {r.n_trades:6d} "
            f"{_fmt(r.expectancy_r):>8s} {pf:>7s} {_fmt(r.ci_low):>9s} "
            f"{_fmt(r.p_value, 4):>7s} {r.verdict:<20s} {action}"
        )


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="Símbolo MT5, p. ej. XAUUSDM")
    parser.add_argument("--bars", type=int, default=50_000)
    parser.add_argument("--spread-bps", type=float, required=True)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--subperiods", type=int, default=3)
    parser.add_argument("--min-trades", type=int, default=30)
    parser.add_argument("--min-signals", type=int, default=30)
    parser.add_argument("--fdr-q", type=float, default=0.10)
    parser.add_argument("--only", help="Coma-separada: sólo estas estrategias")
    parser.add_argument(
        "--lift-loss-streak-halt",
        action="store_true",
        help=(
            "Pone max_consecutive_losses a 0 (laboratorio). Con el valor de producción "
            "el Risk Manager se bloquea de forma permanente tras 5 pérdidas seguidas y "
            "no queda muestra que medir."
        ),
    )
    parser.add_argument("--out", default="data/backtesting/session_edge.json")
    args = parser.parse_args()

    for noisy in ("app.execution", "app.engine", "app.backtesting"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    base = get_settings()
    discovered = sorted(c.name for c in PluginLoader(list(base.quant.plugin_dirs)).discover())
    excluded = [n for n in discovered if n in ORDER_FLOW_EXCLUDED]
    names = [n for n in discovered if n not in ORDER_FLOW_EXCLUDED]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in wanted]

    print(f"Descargando {args.bars} velas 1m de {args.symbol} desde MT5...")
    candles = pull_mt5_candles(args.symbol, args.bars)
    span = f"{candles[0].start:%Y-%m-%d %H:%M}  ->  {candles[-1].end:%Y-%m-%d %H:%M}"
    print(f"  {len(candles)} velas  {span}")
    print(f"\nExcluidas por order flow ausente en MT5: {', '.join(excluded) or '(ninguna)'}")
    print(f"Estrategias analizadas: {len(names)}\n")

    per_strategy = {}
    for i, name in enumerate(names, start=1):
        started = time.monotonic()
        trial = isolate(base, name, discovered, lift_loss_streak_halt=args.lift_loss_streak_halt)
        try:
            per_strategy[name] = run_strategy(
                trial, args.symbol, candles, spread_bps=args.spread_bps, balance=args.balance
            )
        except Exception as exc:  # una estrategia rota no corta el barrido
            print(f"  [{i}/{len(names)}] {name}: ERROR {exc!r}")
            continue
        trades, signals = per_strategy[name]
        print(
            f"  [{i}/{len(names)}] {name:24s} {len(trades):5d} ops "
            f"{len(signals):5d} señales  ({time.monotonic() - started:.0f}s)"
        )

    samples = build_samples(per_strategy)
    results = evaluate_cells(
        samples,
        boundaries=subperiod_boundaries(candles, args.subperiods),
        min_trades=args.min_trades,
        min_signals=args.min_signals,
        fdr_q=args.fdr_q,
    )
    print()
    print_table(results)

    summary = verdict_summary(results)
    print(f"\nVEREDICTO: {summary['decision']}")
    print(f"  {summary['why']}")
    print(f"  conteo por categoría: {summary['counts']}")

    payload = {
        "symbol": args.symbol.upper(),
        "bars": len(candles),
        "range": [candles[0].start.isoformat(), candles[-1].end.isoformat()],
        "spread_bps": args.spread_bps,
        "subperiods": args.subperiods,
        "min_trades": args.min_trades,
        "min_signals": args.min_signals,
        "fdr_q": args.fdr_q,
        "excluded_order_flow": excluded,
        "loss_streak_halt_lifted": args.lift_loss_streak_halt,
        "cells": [r.to_dict() for r in results],
        "summary": summary,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nEscrito: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backtest del QuantCore real sobre histórico de MT5 (se corre en la VPS).

Jala N velas 1m históricas del terminal MT5 (mismas credenciales del ``.env``),
corre EXACTAMENTE las estrategias/consenso/filtros que operan en vivo sobre esa
serie con el spread real del broker, y reutiliza el Execution Engine (sizing,
stops, riesgo) del backtest. Imprime win rate, profit factor y expectativa —
la respuesta estadística de si la configuración actual tiene edge, ANTES de
seguir quemando trades demo.

Uso (en la VPS, dentro de C:\\Users\\MT5\\QuantEngineV2):
    python -m scripts.backtest_quantcore ETHUSDM --bars 5000 --spread-bps 5.3

No opera, no envía órdenes, no habilita live: sólo lee histórico y simula.
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import Counter

from app.backtesting.mt5_history import pull_candles
from app.backtesting.quant_source import run_quantcore_backtest
from app.config.settings import get_settings
from app.engine.plugins import PluginLoader
from app.market.models import Candle


def _pull_mt5_candles(symbol: str, bars: int) -> list[Candle]:
    """Fetch ``bars`` closed 1m candles for ``symbol`` from the MT5 terminal."""
    import MetaTrader5 as mt5  # noqa: N813  # import perezoso: sólo existe en la VPS

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


class _RejectionTally(logging.Handler):
    """Cuenta los rechazos de orden por regla, leyéndolos del log de ejecución.

    El motor explica cada rechazo desde el 2026-07-23 (``ExecutionEngine._reject``),
    pero este script los silenciaba subiendo ``app.execution`` a ERROR por
    considerarlos "esperados en el backtest". Eso tapaba la pregunta más útil:
    el laboratorio abre ~60x menos operaciones que producción sobre la misma
    ventana, y el motivo está en esas líneas.

    Se lee del log en vez de instrumentar el motor: el objetivo es medir el
    backtest tal como corre hoy, sin cambiarle el comportamiento.
    """

    _PATTERN = re.compile(r"Order rejected \S+ \S+ . ([^:]*):")

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.by_rule: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        """Tally one rejection if the record is one."""
        match = self._PATTERN.search(record.getMessage())
        if match is not None:
            self.by_rule[match.group(1).strip() or "(sin regla)"] += 1


def _print_tally(tally: _RejectionTally, trades: int) -> None:
    """Print the rejection breakdown next to the trades that did get through."""
    total = sum(tally.by_rule.values())
    print()
    print("===== POR QUE NO ABRE (rechazos de orden) =====")
    print(f"  Operaciones abiertas:  {trades}")
    print(f"  Ordenes rechazadas:    {total}")
    if total:
        share = trades / (trades + total) * 100.0
        print(f"  Tasa de apertura:      {share:.1f}% de los intentos")
        for rule, count in tally.by_rule.most_common():
            print(f"    {rule:<32} {count:>6}  ({count / total * 100.0:.1f}%)")
    else:
        print("    (ningun rechazo: el motor no llego a intentar abrir)")
    print("===============================================")


def _apply_operator_config(settings: object) -> list[str]:
    """Apply the Config Center overrides that production actually runs with.

    ``get_settings()`` sólo lee el ``.env``. Los cambios que el operador hace
    desde el dashboard viven en ``logs/runtime_config.json`` y se aplican en
    ``bootstrap.build_container`` — por donde el backtest nunca pasa. Resultado:
    el laboratorio corría con frenos de pérdida del 3/8/15 % y kill switch al
    20 % mientras producción los tiene al 1000 % y con
    ``ignore_drawdown_limits``, y con cinco estrategias que producción tiene
    apagadas. No es un matiz de configuración: es la razón de que el backtest
    abriera 22 operaciones donde producción abrió 1.693.

    Returns:
        Descripción de los overrides aplicados, para dejarlos por escrito en la
        salida: un backtest que corre con otra configuración y no lo dice es
        exactamente como se llega a conclusiones que no valen.
    """
    from app.config.settings import QuantStrategySettings, Settings
    from app.dashboard.api.config_store import config_store

    assert isinstance(settings, Settings)
    applied: list[str] = []
    config_store.reapply(settings)
    for key, entry in sorted(config_store.effective(settings).items()):
        if entry.get("overridden"):
            applied.append(f"{key} = {entry.get('value')!r}")
    for name, changes in sorted(config_store.strategy_overrides().items()):
        # `strategy_settings()` devuelve un objeto nuevo si la estrategia no
        # está declarada en el .env, así que hay que escribir en el dict.
        current = settings.quant.strategies.setdefault(name, QuantStrategySettings())
        if "enabled" in changes:
            current.enabled = bool(changes["enabled"])
            applied.append(f"estrategia {name}.enabled = {current.enabled!r}")
        if "weight" in changes:
            current.weight = float(changes["weight"])
            applied.append(f"estrategia {name}.weight = {current.weight!r}")
    return applied


def _detailed_run(
    settings: object, symbol: str, candles: list[Candle], spread_bps: float, balance: float
) -> int:
    """Run once and report expectancy, exit mix and the cost of the commission.

    Returns:
        Operaciones cerradas, para poder relacionarlas con los rechazos.

    Una sola corrida en vez de dos: la comisión se aísla **sobre las mismas
    operaciones**, comparando `pnl` contra `pnl_gross` de cada `TradeRecord`.
    Comparar dos corridas (con y sin comisión) no vale, porque quitar el coste
    cambia la curva de equity, que cambia el sizing, que cambia qué operaciones
    ocurren: son poblaciones distintas, no las mismas con distinto coste. Ese
    fue el error del intento del 2026-08-20.
    """
    from app.backtesting.api import BacktestLab
    from app.backtesting.quant_source import QuantCoreDecisionSource
    from app.config.settings import Settings

    assert isinstance(settings, Settings)
    lab = BacktestLab(settings)
    source = QuantCoreDecisionSource(settings, symbol, spread_bps=spread_bps)
    config = lab.make_config(
        symbol,
        timeframe="1m",
        label=f"{symbol.lower()}-detallado",
        spread_bps=spread_bps,
        initial_balance=balance,
    )
    result = lab.run_backtest(candles, source, config)
    trades = result.trades
    if not trades:
        print("\nSin operaciones cerradas: nada que desglosar.")
        return 0

    # Riesgo en dolares por operacion, deducido del propio registro: es el
    # denominador que convierte dolares en R sin volver a calcular stops.
    def _risk(trade: object) -> float | None:
        r = getattr(trade, "r_multiple", None)
        pnl = getattr(trade, "pnl", None)
        if r is None or pnl is None or abs(float(r)) < 1e-9:
            return None
        risk = abs(float(pnl) / float(r))
        # Una operacion que cierra exactamente en cero deja `risk` en cero y no
        # permite deducir su denominador: se omite en vez de dividir por cero.
        return risk if risk > 1e-12 else None

    net_r: list[float] = []
    gross_r: list[float] = []
    fees = 0.0
    for trade in trades:
        risk = _risk(trade)
        fees += float(getattr(trade, "commission", 0.0) or 0.0)
        if risk is None:
            continue
        net_r.append(float(trade.pnl) / risk)
        gross_r.append(float(getattr(trade, "pnl_gross", trade.pnl)) / risk)

    print("\n===== COMISION, SOBRE LAS MISMAS OPERACIONES =====")
    print(f"  Operaciones con R medible: {len(net_r)} de {len(trades)}")
    if net_r:
        exp_net = sum(net_r) / len(net_r)
        exp_gross = sum(gross_r) / len(gross_r)
        print(f"  Expectativa NETA  (como la cobra el lab): {exp_net:+.4f}R")
        print(f"  Expectativa BRUTA (como cobra Exness):    {exp_gross:+.4f}R")
        print(f"  Coste de la comision:                     {exp_gross - exp_net:+.4f}R")
    print(f"  Comision total pagada: {fees:,.2f} en {len(trades)} operaciones")
    print("=================================================")

    exits: Counter[str] = Counter()
    exit_r: dict[str, list[float]] = {}
    for trade in trades:
        reason = str(getattr(trade, "exit_reason", "?") or "?")
        exits[reason] += 1
        risk = _risk(trade)
        if risk is not None:
            exit_r.setdefault(reason, []).append(float(trade.pnl) / risk)
    print("\n===== POR DONDE SALE =====")
    for reason, count in exits.most_common():
        rs = exit_r.get(reason, [])
        mean = sum(rs) / len(rs) if rs else 0.0
        share = count / len(trades) * 100.0
        print(f"  {reason:<20} {count:>6} ({share:>5.1f}%)  R medio {mean:+.3f}")
    print("==========================")
    return len(trades)


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Backtest del QuantCore real sobre histórico MT5")
    parser.add_argument("symbol", help="Símbolo, p. ej. ETHUSDM")
    parser.add_argument("--bars", type=int, default=5000, help="Velas 1m a jalar (default 5000)")
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=5.3,
        help="Spread real del broker en bps (default 5.3, el de ETH en Exness)",
    )
    parser.add_argument("--balance", type=float, default=1000.0, help="Balance inicial")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Desglosa por que el motor no abre (rechazos de orden por regla)",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Una sola corrida con desglose de comision (misma poblacion) y de salidas",
    )
    parser.add_argument(
        "--solo",
        default="",
        help="Corre UNA sola estrategia, desactivando las demas en el plugin loader",
    )
    parser.add_argument(
        "--live-config",
        action="store_true",
        help="Corre con los overrides del Config Center (logs/runtime_config.json), "
        "que es la configuracion con la que opera produccion de verdad",
    )
    args = parser.parse_args()

    exec_log = logging.getLogger("app.execution")
    tally = _RejectionTally()
    if args.diagnose:
        exec_log.setLevel(logging.WARNING)
        exec_log.addHandler(tally)
        # El desglose es el resultado; las lineas sueltas solo estorbarian.
        exec_log.propagate = False
    else:
        # Silencia el ruido de rechazos por riesgo (esperados en el backtest).
        exec_log.setLevel(logging.ERROR)

    settings = get_settings()
    settings.quant.enabled = True
    if args.live_config:
        applied = _apply_operator_config(settings)
        print(f"Configuracion del operador aplicada ({len(applied)} overrides):")
        for line in applied:
            print(f"  - {line}")

    if args.solo:
        # Aislar una estrategia: el consenso promedia 20, y varias estan
        # correlacionadas entre si, asi que el voto de cualquiera queda diluido.
        # Se desactivan en el loader (no con `strategies_enabled`, que solo
        # bloquea la apertura): aqui interesa que ni siquiera voten.
        from app.config.settings import QuantStrategySettings

        loader = PluginLoader(list(settings.quant.plugin_dirs))
        apagadas = 0
        for cls in loader.discover():
            if cls.name == args.solo:
                continue
            settings.quant.strategies.setdefault(cls.name, QuantStrategySettings()).enabled = False
            apagadas += 1
        settings.quant.strategies.setdefault(args.solo, QuantStrategySettings()).enabled = True
        print(f"Solo {args.solo}: {apagadas} estrategias desactivadas en el loader")

    print(f"Jalando {args.bars} velas 1m de {args.symbol} desde MT5...")
    candles = _pull_mt5_candles(args.symbol, args.bars)
    span = f"{candles[0].start:%Y-%m-%d %H:%M} → {candles[-1].start:%Y-%m-%d %H:%M}"
    print(f"  {len(candles)} velas: {span}")

    if args.detail:
        print(f"Corriendo backtest detallado (spread {args.spread_bps} bps)...")
        opened = _detailed_run(settings, args.symbol, candles, args.spread_bps, args.balance)
        if args.diagnose:
            _print_tally(tally, opened)
        return

    print(f"Corriendo backtest (spread {args.spread_bps} bps + spread 0 de control)...")
    r = run_quantcore_backtest(
        settings, args.symbol, candles, spread_bps=args.spread_bps, balance=args.balance
    )
    print("\n===== RESULTADO (spread real) =====")
    print(f"  Trades:          {r['trades']}")
    print(f"  Win rate:        {r['win_rate_pct']}%")
    print(f"  Profit factor:   {r['profit_factor']}")
    print(f"  Expectativa (R): {r['expectancy_r']}")
    print(f"  Retorno neto:    {r['return_pct']}%")
    print(f"  Max drawdown:    {r['max_drawdown_pct']}%")
    print("----- control (spread 0) -----")
    print(f"  Trades:          {r['zero_spread_trades']}")
    print(f"  Win rate:        {r['zero_spread_win_rate_pct']}%")
    print(f"  Profit factor:   {r['zero_spread_profit_factor']}")
    print(f"  Retorno neto:    {r['zero_spread_return_pct']}%")
    print("===================================")
    if args.diagnose:
        # Ambas corridas (spread real y spread 0) comparten el contador: lo que
        # se mide aqui es por que el motor no abre, y eso no depende del spread.
        _print_tally(tally, int(r["trades"]) + int(r["zero_spread_trades"]))
    if int(r["trades"]) < 30:
        print("\n⚠  Menos de 30 trades: muestra insuficiente para concluir. Sube --bars.")


if __name__ == "__main__":
    main()

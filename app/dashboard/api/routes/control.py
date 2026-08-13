"""Command endpoints: strategy control and backtest submission (all audited).

Strategy control aplica sobre el ``StrategyEngine`` vivo **y** persiste la
decisión en el almacén de configuración, que el motor reaplica tras el
descubrimiento en cada arranque. Antes sólo se persistía, y nadie leía esa
persistencia: el dashboard decía "Strategy disabled" y la estrategia seguía
evaluando indefinidamente. Un endpoint que sólo audita la intención es
indistinguible, desde fuera, de uno que no hace nada.

Backtests are heavy and execute server-side via the BacktestLab; this accepts and
audits the request. No endpoint here can enable live trading.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.config.settings import QuantStrategySettings
from app.core.container import Container
from app.core.exceptions import ConfigurationError
from app.dashboard.api.audit import audit_log
from app.dashboard.api.config_store import config_store
from app.engine.quant_core import QuantCore
from app.engine.strategy_engine import StrategyEngine
from app.utils.time import utc_now

router = APIRouter(tags=["control"])
_log = logging.getLogger("app.dashboard.backtest")


def _strategies(request: Request) -> StrategyEngine:
    """StrategyEngine vivo, o 503 si el Quant Core está apagado.

    Se llega por el ``QuantCore`` —igual que ``GET /api/engine/strategies``—
    para garantizar que el control y la lectura operan sobre el mismo objeto:
    si difirieran, la tabla podría seguir pintando "enabled" después de un
    disable correcto, que es justo el fallo que este endpoint venía a corregir.
    """
    container: Container | None = request.app.state.container
    if container is None or not container.contains(QuantCore):
        raise HTTPException(status_code=503, detail="Quant Core not enabled")
    return container.resolve(QuantCore).strategies


def _state(engine: StrategyEngine, name: str) -> dict[str, Any]:
    """Estado efectivo de una estrategia, para que la UI pinte lo que es."""
    for stats in engine.stats():
        if stats.name == name:
            return {"enabled": stats.enabled, "weight": stats.weight}
    return {}


@router.post("/engine/strategies/{name}/enable")
async def enable_strategy(name: str, request: Request) -> dict[str, Any]:
    """Enable a loaded strategy now and persist the decision (audited)."""
    engine = _strategies(request)
    try:
        engine.enable_strategy(name)
    except ConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    override = config_store.set_strategy(name, {"enabled": True})
    audit_log.record(action="strategy.enable", target=name, after=override)
    return {"strategy": name, "override": override, "applied": _state(engine, name)}


@router.post("/engine/strategies/{name}/disable")
async def disable_strategy(name: str, request: Request) -> dict[str, Any]:
    """Disable a loaded strategy now and persist the decision (audited)."""
    engine = _strategies(request)
    try:
        engine.disable_strategy(name)
    except ConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    override = config_store.set_strategy(name, {"enabled": False})
    audit_log.record(action="strategy.disable", target=name, after=override)
    return {"strategy": name, "override": override, "applied": _state(engine, name)}


@router.patch("/engine/strategies/{name}/weight")
async def set_strategy_weight(
    name: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Set a strategy's consensus weight now and persist it (audited)."""
    engine = _strategies(request)
    try:
        weight = float(payload.get("weight", 1.0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="'weight' debe ser numérico") from exc
    if weight < 0:
        raise HTTPException(status_code=422, detail="'weight' no puede ser negativo")
    try:
        applied = engine.set_weight(name, weight)
    except ConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    override = config_store.set_strategy(name, {"weight": applied})
    audit_log.record(action="strategy.weight", target=name, after=override)
    return {"strategy": name, "override": override, "applied": _state(engine, name)}


def _parse_day(value: Any, field: str, *, end_of_day: bool) -> datetime | None:
    """Parsear una fecha ``YYYY-MM-DD`` del formulario a un instante UTC.

    ``end`` se toma **inclusivo**: quien escribe 12/08 en un selector de fecha
    espera que el 12/08 entre, no que el rango muera a medianoche del 11.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"'{field}' debe tener formato YYYY-MM-DD"
        ) from exc
    return day + timedelta(days=1) if end_of_day else day


def _only_strategy(settings: Any, strategy: str) -> Any:
    """Copia de settings donde sólo ``strategy`` queda habilitada.

    Se apagan las demás por configuración en vez de filtrar el catálogo: es el
    mismo interruptor que usa el motor en vivo (``QuantSettings.strategies``),
    así que la corrida aislada pasa por exactamente el mismo camino.

    Raises:
        HTTPException: 422 si la estrategia no existe en el catálogo.
    """
    from app.engine.plugins import PluginLoader

    trial = settings.model_copy(deep=True)
    known = {cls.name for cls in PluginLoader(list(trial.quant.plugin_dirs)).discover()}
    if strategy not in known:
        raise HTTPException(status_code=422, detail=f"Estrategia desconocida: '{strategy}'")
    for name in known:
        current = trial.quant.strategies.get(name)
        config = current.model_copy() if current is not None else QuantStrategySettings()
        config.enabled = name == strategy
        trial.quant.strategies[name] = config
    return trial


def _run_backtest_blocking(
    settings: Any,
    symbol: str,
    bars: int,
    spread_bps: float,
    candles: list[Any],
    strategy: str | None,
) -> dict[str, Any]:
    """Execute the real-QuantCore backtest and record it as an experiment."""
    from app.backtesting.api import BacktestLab
    from app.backtesting.quant_source import run_quantcore_backtest

    # El backtest genera muchos rechazos por riesgo (esperados); no ensuciar logs.
    logging.getLogger("app.execution").setLevel(logging.ERROR)
    result = run_quantcore_backtest(settings, symbol, candles, spread_bps=spread_bps)
    # La corrida debe poder identificarse después: sin el alcance en la etiqueta,
    # dos experimentos con recortes distintos quedaban indistinguibles en la lista.
    scope = strategy or "all"
    first, last = candles[0].start, candles[-1].start
    try:
        BacktestLab(settings).save_experiment(
            label=f"{symbol.lower()}-quantcore-{scope}-dashboard",
            result=result,
            dataset=f"mt5:{symbol}:{len(candles)}x1m",
            notes=(
                f"Backtest QuantCore real lanzado desde el dashboard. "
                f"Estrategias: {scope}. "
                f"Rango: {first.isoformat()} → {last.isoformat()} "
                f"({len(candles)} de {bars} velas pedidas)."
            ),
        )
    except Exception:  # pragma: no cover - persistencia best-effort
        _log.warning("No se pudo guardar el experimento del backtest", exc_info=True)
    result["strategy"] = scope
    result["candles"] = len(candles)
    result["range_start"] = first.isoformat()
    result["range_end"] = last.isoformat()
    return result


@router.post("/backtesting/run")
async def submit_backtest(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Run a real QuantCore backtest over MT5 history (audited).

    Corre las estrategias reales sobre el histórico 1m del terminal MT5 vivo, a
    spread real y a spread 0 (control), y guarda el resultado como experimento.
    Pesado pero acotado: se ejecuta en un hilo para no bloquear el event loop.

    Acepta ``strategy`` (aislar una del catálogo) y ``start``/``end`` (recortar
    el rango). Antes se ignoraban en silencio: el formulario los pedía —y
    exigía ``strategy`` para habilitar el botón— mientras la corrida usaba
    siempre las 20 estrategias sobre las últimas 5000 velas.
    """
    from app.backtesting.mt5_history import pull_candles
    from app.brokers.mt5.connection import MT5Connection
    from app.config.settings import Settings

    audit_log.record(action="backtest.run", after=payload)
    container = request.app.state.container
    if container is None or not container.contains(MT5Connection):
        raise HTTPException(status_code=503, detail="MT5 no disponible (solo modo demo)")

    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=422, detail="Falta 'symbol'")
    bars = int(payload.get("bars") or 5000)
    strategy = str(payload.get("strategy") or "").strip() or None
    start = _parse_day(payload.get("start"), "start", end_of_day=False)
    end = _parse_day(payload.get("end"), "end", end_of_day=True)
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="'start' debe ser anterior a 'end'")

    settings = container.resolve(Settings)
    spread_bps = float(payload.get("spread_bps") or settings.backtesting.default_spread_bps or 5.3)
    settings.quant.enabled = True
    if strategy is not None:
        settings = _only_strategy(settings, strategy)
        settings.quant.enabled = True

    conn = container.resolve(MT5Connection)
    try:
        with conn.lock:
            candles = pull_candles(conn.mt5, symbol, bars, resolve=conn.resolve_symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Histórico MT5: {exc}") from exc

    # El recorte se hace aquí y no en la lectura porque MT5 sólo sabe entregar
    # "las N más recientes"; si el rango pedido queda fuera de lo que el
    # terminal guarda (~35 días de 1m), hay que decirlo en vez de devolver otro
    # periodo y que pase por el pedido.
    if start is not None or end is not None:
        candles = [
            candle
            for candle in candles
            if (start is None or candle.start >= start) and (end is None or candle.start < end)
        ]
    if not candles:
        raise HTTPException(
            status_code=422,
            detail=(
                "El rango pedido no tiene velas en el histórico de MT5 "
                f"(se leyeron las últimas {bars} velas de 1m; el terminal guarda ~35 días)."
            ),
        )

    result = await asyncio.to_thread(
        _run_backtest_blocking, settings, symbol, bars, spread_bps, candles, strategy
    )
    result["submitted_at"] = utc_now().isoformat()
    return result


@router.post("/backtesting/cancel/{job_id}")
async def cancel_backtest(job_id: str) -> dict[str, Any]:
    """Reject cancellation: backtests run synchronously and cannot be stopped.

    Devolvía ``{"status": "cancelled"}`` sin cancelar nada. Una corrida se
    ejecuta entera dentro de la propia petición (``asyncio.to_thread``), así que
    no hay job al que llegar. Un 409 honesto es mejor que un OK falso: mientras
    decía "cancelado", el backtest seguía consumiendo CPU hasta terminar.
    """
    audit_log.record(action="backtest.cancel", target=job_id)
    raise HTTPException(
        status_code=409,
        detail=(
            "Los backtests corren de forma síncrona dentro de la petición y no se "
            "pueden cancelar. Cierra la pestaña o espera a que termine."
        ),
    )

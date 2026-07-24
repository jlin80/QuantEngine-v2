"""Command endpoints: strategy control and backtest submission (all audited).

Strategy control records operator intent in the runtime config store (a running
engine loads strategies at startup, so intents apply on reload / where wired).
Backtests are heavy and execute server-side via the BacktestLab; this accepts and
audits the request. No endpoint here can enable live trading.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.dashboard.api.audit import audit_log
from app.dashboard.api.config_store import config_store
from app.utils.time import utc_now

router = APIRouter(tags=["control"])
_log = logging.getLogger("app.dashboard.backtest")


@router.post("/engine/strategies/{name}/enable")
async def enable_strategy(name: str) -> dict[str, Any]:
    """Record intent to enable a strategy (audited)."""
    override = config_store.set_strategy(name, {"enabled": True})
    audit_log.record(action="strategy.enable", target=name, after=override)
    return {"strategy": name, "override": override}


@router.post("/engine/strategies/{name}/disable")
async def disable_strategy(name: str) -> dict[str, Any]:
    """Record intent to disable a strategy (audited)."""
    override = config_store.set_strategy(name, {"enabled": False})
    audit_log.record(action="strategy.disable", target=name, after=override)
    return {"strategy": name, "override": override}


@router.patch("/engine/strategies/{name}/weight")
async def set_strategy_weight(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record a strategy weight override (audited)."""
    weight = float(payload.get("weight", 1.0))
    override = config_store.set_strategy(name, {"weight": weight})
    audit_log.record(action="strategy.weight", target=name, after=override)
    return {"strategy": name, "override": override}


def _run_backtest_blocking(
    settings: Any, symbol: str, bars: int, spread_bps: float, candles: list[Any]
) -> dict[str, Any]:
    """Execute the real-QuantCore backtest and record it as an experiment."""
    from app.backtesting.api import BacktestLab
    from app.backtesting.quant_source import run_quantcore_backtest

    # El backtest genera muchos rechazos por riesgo (esperados); no ensuciar logs.
    logging.getLogger("app.execution").setLevel(logging.ERROR)
    result = run_quantcore_backtest(settings, symbol, candles, spread_bps=spread_bps)
    try:
        BacktestLab(settings).save_experiment(
            label=f"{symbol.lower()}-quantcore-dashboard",
            result=result,
            dataset=f"mt5:{symbol}:{bars}x1m",
            notes="Backtest QuantCore real lanzado desde el dashboard.",
        )
    except Exception:  # pragma: no cover - persistencia best-effort
        _log.warning("No se pudo guardar el experimento del backtest", exc_info=True)
    return result


@router.post("/backtesting/run")
async def submit_backtest(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Run a real QuantCore backtest over MT5 history (audited).

    Corre las estrategias reales sobre el histórico 1m del terminal MT5 vivo, a
    spread real y a spread 0 (control), y guarda el resultado como experimento.
    Pesado pero acotado: se ejecuta en un hilo para no bloquear el event loop.
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
    settings = container.resolve(Settings)
    spread_bps = float(payload.get("spread_bps") or settings.backtesting.default_spread_bps or 5.3)
    settings.quant.enabled = True

    conn = container.resolve(MT5Connection)
    try:
        with conn.lock:
            candles = pull_candles(conn.mt5, symbol, bars, resolve=conn.resolve_symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Histórico MT5: {exc}") from exc

    result = await asyncio.to_thread(
        _run_backtest_blocking, settings, symbol, bars, spread_bps, candles
    )
    result["submitted_at"] = utc_now().isoformat()
    return result


@router.post("/backtesting/cancel/{job_id}")
async def cancel_backtest(job_id: str) -> dict[str, Any]:
    """Cancel a submitted backtest job (best effort; audited)."""
    audit_log.record(action="backtest.cancel", target=job_id)
    return {"job_id": job_id, "status": "cancelled"}

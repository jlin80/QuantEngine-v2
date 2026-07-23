"""Pruebas de la selección de broker por modo (paper/demo/live).

Verifican que ``demo`` construye el ``MT5Broker`` real (con conexión), que sin
conexión da un error accionable, y que ``live`` sigue prohibido.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.brokers.mt5.broker import MT5Broker
from app.brokers.mt5.connection import MT5Connection, MT5ConnectionConfig
from app.config.settings import ExecutionSettings
from app.core.exceptions import ConfigurationError
from app.engine.bootstrap import _resolve_initial_balance
from app.execution.commission import CommissionEngine
from app.execution.latency import LatencyEngine
from app.execution.paper_engine import PaperBroker
from app.execution.slippage import SlippageEngine
from app.production.live.broker_selector import select_broker


class _BalanceMT5:
    """MT5 falso mínimo que sólo sirve initialize/account_info/last_error."""

    def __init__(self, balance: float | None) -> None:
        self._balance = balance

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def account_info(self) -> Any:
        return None if self._balance is None else SimpleNamespace(balance=self._balance)


def _demo_conn(balance: float | None) -> MT5Connection:
    return MT5Connection(
        MT5ConnectionConfig(login=1, password="x", server="s"), mt5=_BalanceMT5(balance)
    )


def _engines(settings: ExecutionSettings) -> tuple[CommissionEngine, SlippageEngine, LatencyEngine]:
    return (
        CommissionEngine(settings.commission),
        SlippageEngine(settings.slippage),
        LatencyEngine(settings.latency),
    )


def test_paper_mode_builds_paper_broker() -> None:
    settings = ExecutionSettings()
    commission, slippage, latency = _engines(settings)
    broker = select_broker("paper", settings, commission, slippage, latency)
    assert isinstance(broker, PaperBroker)


def test_demo_mode_builds_mt5_broker() -> None:
    settings = ExecutionSettings()
    commission, slippage, latency = _engines(settings)
    conn = MT5Connection(MT5ConnectionConfig(login=1, password="x", server="s"))
    broker = select_broker("demo", settings, commission, slippage, latency, mt5_connection=conn)
    assert isinstance(broker, MT5Broker)
    assert broker.broker_name == "mt5_demo"


def test_demo_without_connection_raises() -> None:
    settings = ExecutionSettings()
    commission, slippage, latency = _engines(settings)
    with pytest.raises(ConfigurationError):
        select_broker("demo", settings, commission, slippage, latency)


def test_live_mode_still_forbidden() -> None:
    settings = ExecutionSettings()
    commission, slippage, latency = _engines(settings)
    with pytest.raises(ConfigurationError):
        select_broker("live", settings, commission, slippage, latency)


def test_execution_settings_resolves_demo() -> None:
    assert ExecutionSettings(mode="demo").resolved_mode() == "demo"
    assert ExecutionSettings(mode="paper").resolved_mode() == "paper"
    # 'live' nunca se resuelve por config: cae a paper.
    assert ExecutionSettings(mode="live").resolved_mode() == "paper"


def test_execution_settings_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match=r"paper.*demo.*live"):
        ExecutionSettings(mode="lvie")


def test_connection_reads_account_balance() -> None:
    conn = _demo_conn(2500.0)
    conn.connect()
    assert conn.account_balance() == pytest.approx(2500.0)


class _CaseMT5:
    """MT5 falso: el símbolo real es 'XAUUSDm' pero el sistema pide 'XAUUSDM'."""

    def symbol_info(self, symbol: str) -> Any:
        return None

    def symbols_get(self) -> list[Any]:
        return [SimpleNamespace(name="XAUUSDm"), SimpleNamespace(name="EURUSD")]


def test_resolve_symbol_case_insensitive() -> None:
    conn = MT5Connection(MT5ConnectionConfig(login=1, password="x", server="s"), mt5=_CaseMT5())
    assert conn.resolve_symbol("XAUUSDM") == "XAUUSDm"  # mapea a la caja real
    assert conn.resolve_symbol("XAUUSDM") == "XAUUSDm"  # cacheado
    assert conn.resolve_symbol("NOPE") == "NOPE"  # sin coincidencia: tal cual


def test_demo_seeds_balance_from_broker() -> None:
    execution = ExecutionSettings(mode="demo", use_broker_balance=True, initial_balance=1000.0)
    balance = _resolve_initial_balance(execution, _demo_conn(7777.0))
    assert balance == pytest.approx(7777.0)


def test_demo_falls_back_when_balance_unavailable() -> None:
    execution = ExecutionSettings(mode="demo", use_broker_balance=True, initial_balance=1234.0)
    balance = _resolve_initial_balance(execution, _demo_conn(None))
    assert balance == pytest.approx(1234.0)


def test_demo_respects_disabled_broker_balance() -> None:
    execution = ExecutionSettings(mode="demo", use_broker_balance=False, initial_balance=500.0)
    balance = _resolve_initial_balance(execution, _demo_conn(9999.0))
    assert balance == pytest.approx(500.0)


def test_paper_ignores_broker_balance() -> None:
    execution = ExecutionSettings(mode="paper", use_broker_balance=True, initial_balance=800.0)
    balance = _resolve_initial_balance(execution, _demo_conn(9999.0))
    assert balance == pytest.approx(800.0)

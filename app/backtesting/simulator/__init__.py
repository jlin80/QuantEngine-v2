"""Simulador: reutiliza el Execution Engine real dentro del backtest (Fase 6)."""

from app.backtesting.simulator.execution_factory import ExecutionStack, build_execution_stack

__all__ = ["ExecutionStack", "build_execution_stack"]

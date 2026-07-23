"""Order Manager: ciclo de vida de las órdenes (incluye estructura OCO)."""

from app.execution.order_manager.manager import OcoGroup, OrderManager

__all__ = ["OcoGroup", "OrderManager"]

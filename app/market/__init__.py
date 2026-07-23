"""Data Engine — capa de datos de mercado (Fase 2).

Única fuente de verdad de datos de mercado del sistema. Ningún módulo se
conecta a un broker directamente: los proveedores viven detrás de
:class:`~app.market.feed.MarketFeed` y todo consumidor usa
:class:`~app.market.services.MarketDataService` o los eventos del bus.
"""

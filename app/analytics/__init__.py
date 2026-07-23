"""Analítica de mercado: indicadores puros, deterministas y sin I/O.

Este paquete NO depende de nada fuera de ``app.market.models`` y
``app.utils``: recibe velas/trades/libros y devuelve estructuras tipadas.
Lo consumen el Feature Store (cálculo único) y las estrategias (Fase 4).
"""

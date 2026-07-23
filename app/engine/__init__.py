"""Motor: composición de dependencias, orquestación y Quant Core (Fase 3).

Importar desde los submódulos (``app.engine.bootstrap``, ``app.engine.engine``,
``app.engine.quant_core``...) — este paquete no re-exporta nada para evitar
ciclos con el dashboard (las rutas importan ``app.engine.*`` y el bootstrap
importa ``app.dashboard.api.main``).
"""

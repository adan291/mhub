"""Registro de módulos activos del bot.

Para añadir un módulo nuevo:
  1. Crea un paquete en modules/ con una función `async def setup(bot)`.
  2. (Opcional) añade `async def restore(bot)` para estado persistente.
  3. Impórtalo aquí y añádelo a ENABLED_MODULES.

IMPORTANTE — orden de carga: el módulo `api` debe ir SIEMPRE el último. Su
`setup` construye la app FastAPI capturando en `app.state` (vía getattr) las
dependencias ya presentes en el bot (`signup_manager`, `registry_store`,
`event_bus`, `bridge_service`, `guild_config_store`). Si `api` se cargara antes
que algún módulo proveedor (p. ej. `bridge`), esa dependencia quedaría como
`None` y su endpoint asociado fallaría. Por eso: proveedores primero, `api` al
final.
"""
from . import signups, registry, bridge, api

ENABLED_MODULES = [signups, registry, bridge, api]

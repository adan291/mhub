"""Registro de módulos activos del bot.

Para añadir un módulo nuevo:
  1. Crea un paquete en modules/ con una función `async def setup(bot)`.
  2. (Opcional) añade `async def restore(bot)` para estado persistente.
  3. Impórtalo aquí y añádelo a ENABLED_MODULES.
"""
from . import signups, registry, api, bridge

ENABLED_MODULES = [signups, registry, api, bridge]

"""Módulo puente skills↔grupos (Modulo_Puente).

Expone `BridgeService`, que relaciona los roles de un Evento con las Personas
del directorio, y lo integra con el bot vía `setup(bot)` junto con el slash
command de sugerencias por evento (Req 4.1, 13.3, 13.4).
"""
from .service import BridgeService, Persona
from .cog import BridgeCog

__all__ = ["BridgeService", "Persona", "BridgeCog"]


async def setup(bot):
    """Integra el Modulo_Puente sin tocar el núcleo (Req 13.3, 13.4).

    Deriva todo de los stores ya cargados por los módulos `registry` y
    `signups`, por lo que este módulo debe registrarse después de ellos en
    `ENABLED_MODULES`.
    """
    service = BridgeService(bot.registry_store, bot.signup_manager.store)
    bot.bridge_service = service
    await bot.add_cog(BridgeCog(bot, service, bot.channel_id))

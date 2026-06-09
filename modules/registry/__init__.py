"""Módulo de directorio/registro por categorías (ex-mprof)."""
from core import config
from core.guild_config_store import get_or_create_guild_config_store
from .store import RegistryStore
from .cog import RegistryCog


async def setup(bot):
    cfg = config.Config.load_json("registry.json")
    db_path = config.Config.get_db_path("registry.sqlite")
    store = RegistryStore(db_path)
    bot.registry_store = store
    # Store compartido de Configuracion_Por_Servidor: las categorías de
    # `registry.json` son la configuración por defecto con fallback (Req 3.3).
    guild_config_store = get_or_create_guild_config_store(
        bot, default_categories=cfg["categorias"])
    await bot.add_cog(
        RegistryCog(bot, store, cfg, guild_config_store, db_path, bot.channel_id))

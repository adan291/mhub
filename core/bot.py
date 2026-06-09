import discord
from discord.ext import commands

from core import config
from modules import ENABLED_MODULES


class HubBot(commands.Bot):
    """Bot modular. Cada módulo de `modules/` aporta sus comandos.

    Contrato de un módulo (su paquete debe exponer):
      - async def setup(bot):    registra cogs y deja sus dependencias listas.
      - async def restore(bot):  (opcional) restaura estado tras un reinicio,
                                 p. ej. vistas persistentes de botones.
    """

    def __init__(self, intents, channel_id=None, guild_id=None):
        super().__init__(command_prefix='!', intents=intents)
        self.channel_id = channel_id
        self.guild_id = guild_id

    async def setup_hook(self):
        for module in ENABLED_MODULES:
            await module.setup(self)
            print(f"Módulo cargado: {module.__name__}")

        # Restauración de estado (vistas persistentes, etc.)
        for module in ENABLED_MODULES:
            restore = getattr(module, "restore", None)
            if restore:
                await restore(self)

        await self._sync_commands()

    async def _sync_commands(self):
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Comandos sincronizados en el servidor {self.guild_id}.")
        else:
            await self.tree.sync()
            print("Comandos sincronizados globalmente.")

    async def on_ready(self):
        print(f'{self.user} ha iniciado sesión.')

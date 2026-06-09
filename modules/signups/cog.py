import copy

import discord
from discord import app_commands
from discord.ext import commands

from .composition_parser import parse_composition
from .embed import build_embed
from .manager import CompositionError
from .views import EventView


class SignupCog(commands.Cog):
    def __init__(self, bot, manager, guild_config_store, allowed_channel_id=None):
        self.bot = bot
        self.manager = manager
        self.guild_config_store = guild_config_store
        self.allowed_channel_id = allowed_channel_id

    def _resolve_templates(self, interaction: discord.Interaction):
        """Plantillas aplicables al Servidor de la interacción, con fallback a
        las plantillas por defecto cuando el Servidor no tiene config propia
        (Req 3.2, 3.3)."""
        return self.guild_config_store.resolve_templates(interaction.guild_id)

    async def _plantilla_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=key, value=key)
            for key in self._resolve_templates(interaction).keys()
            if current.lower() in key.lower()
        ][:25]

    @app_commands.command(name="evento", description="Crea un evento de inscripción con roles.")
    @app_commands.describe(
        plantilla="Plantilla de roles a usar (p. ej. mitica, raid, dnd).",
        titulo="Título del evento. Si se omite, se usa el de la plantilla.",
    )
    @app_commands.autocomplete(plantilla=_plantilla_autocomplete)
    async def evento(self, interaction: discord.Interaction, plantilla: str, titulo: str = None):
        if self.allowed_channel_id and interaction.channel_id != self.allowed_channel_id:
            await interaction.response.send_message(
                f"Este comando solo se puede usar en <#{self.allowed_channel_id}>.", ephemeral=True)
            return

        templates = self._resolve_templates(interaction)
        template = templates.get(plantilla)
        if not template:
            disponibles = ", ".join(templates.keys())
            await interaction.response.send_message(
                f"Plantilla **{plantilla}** no válida. Disponibles: {disponibles}.", ephemeral=True)
            return

        title = titulo or template["titulo"]

        # La Composicion del evento se copia de la plantilla resuelta para el
        # Servidor (Req 2.6, 3.2). Copia profunda para no mutar la config
        # compartida.
        composition = copy.deepcopy(template["roles"])
        placeholder_event = {
            "title": title,
            "state": {role_key: [] for role_key in composition},
        }
        embed = build_embed(placeholder_event, template)
        view = EventView(self.manager, plantilla, template=template)

        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()

        self.manager.create(
            message.id, interaction.channel_id, interaction.guild_id, title,
            composition, template_key=plantilla)

    @app_commands.command(
        name="evento_custom",
        description="Crea un evento con una composición de roles a medida.")
    @app_commands.describe(
        roles="Composición 'rol:plazas, ...'. Ej: backend:2, frontend:3, qa:1.",
        titulo="Título del evento.",
    )
    async def evento_custom(self, interaction: discord.Interaction, roles: str, titulo: str):
        if self.allowed_channel_id and interaction.channel_id != self.allowed_channel_id:
            await interaction.response.send_message(
                f"Este comando solo se puede usar en <#{self.allowed_channel_id}>.", ephemeral=True)
            return

        # Parseo y validación uniformes: el parser y el manager lanzan la misma
        # CompositionError, así que un único except cubre formato y reglas de
        # negocio (Req 2.1, 2.3, 2.4, 2.5).
        try:
            pairs = parse_composition(roles)
            composition = self.manager.validate_composition(pairs)
        except CompositionError as exc:
            await interaction.response.send_message(
                f"Composición inválida: {exc}", ephemeral=True)
            return

        # Evento ad-hoc: la Composicion vive con el evento, sin plantilla global
        # (Req 2.1, 15.2). `template` se construye desde la propia composición.
        template = {"roles": composition}
        placeholder_event = {
            "title": titulo,
            "state": {role_key: [] for role_key in composition},
        }
        embed = build_embed(placeholder_event, template)
        view = EventView(self.manager, None, template=template)

        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()

        self.manager.create(
            message.id, interaction.channel_id, interaction.guild_id, titulo,
            composition, template_key=None)

    @app_commands.command(
        name="evento_archivar",
        description="Archiva un evento de inscripción.")
    @app_commands.describe(evento="ID del mensaje del evento a archivar.")
    async def evento_archivar(self, interaction: discord.Interaction, evento: str):
        if self.allowed_channel_id and interaction.channel_id != self.allowed_channel_id:
            await interaction.response.send_message(
                f"Este comando solo se puede usar en <#{self.allowed_channel_id}>.", ephemeral=True)
            return

        # El evento no persiste el user_id de su creador, por lo que no podemos
        # restringir el archivado al autor. Decisión pragmática: lo permitimos a
        # quien tenga permiso para gestionar mensajes o sea administrador del
        # Servidor (Req 11.3, 15.3).
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None or not (perms.manage_messages or perms.administrator):
            await interaction.response.send_message(
                "Necesitas permiso para gestionar mensajes para archivar un evento.",
                ephemeral=True)
            return

        try:
            event_id = int(evento)
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "El identificador del evento debe ser numérico.", ephemeral=True)
            return

        event = self.manager.archive(event_id)
        if event is None:
            await interaction.response.send_message(
                "No se encontró ningún evento con ese identificador.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Evento archivado correctamente.", ephemeral=True)

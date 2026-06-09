"""Cog de Discord para el Modulo_Puente.

Expone un slash command de sugerencias por evento que, para cada rol de la
Composicion del evento, lista las Personas del directorio cuya Skill se
corresponde con el rol (Req 4.1). Los roles sin candidatos se muestran
explícitamente (Req 4.4).

El cog es una capa fina sobre `BridgeService`: no reimplementa el matching, solo
formatea el resultado de `suggest_for_event` para Discord (Req 4.6).
"""
import discord
from discord import app_commands
from discord.ext import commands


class BridgeCog(commands.Cog):
    def __init__(self, bot, bridge_service, allowed_channel_id=None):
        self.bot = bot
        self.bridge = bridge_service
        self.allowed_channel_id = allowed_channel_id

    @app_commands.command(
        name="equipo_sugerencias",
        description="Sugiere Personas del directorio para cada rol de un evento.")
    @app_commands.describe(
        evento="ID del mensaje del evento (message_id).")
    async def equipo_sugerencias(self, interaction: discord.Interaction, evento: str):
        if self.allowed_channel_id and interaction.channel_id != self.allowed_channel_id:
            await interaction.response.send_message(
                f"Este comando solo se puede usar en <#{self.allowed_channel_id}>.",
                ephemeral=True)
            return

        # Los IDs de Discord (snowflakes) exceden el rango de un option entero de
        # slash command, por eso se recibe como texto y se convierte aquí.
        try:
            event_id = int(evento)
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "El ID del evento debe ser numérico (el message_id del evento).",
                ephemeral=True)
            return

        suggestions = self.bridge.suggest_for_event(event_id)
        if not suggestions:
            await interaction.response.send_message(
                "No se encontró ningún evento con ese ID, o no tiene roles.",
                ephemeral=True)
            return

        lines = ["**Sugerencias de equipo por rol:**\n"]
        for role_key, personas in suggestions.items():
            if personas:
                lines.append(f"\n**{role_key}**")
                for persona in personas:
                    lines.append(f"- [{persona.provider}]({persona.link})")
            else:
                # Rol sin candidatos en el directorio (Req 4.4).
                lines.append(f"\n**{role_key}** · _sin candidatos en el directorio_")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

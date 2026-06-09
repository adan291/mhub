import discord
from .embed import build_embed


class RoleButton(discord.ui.Button):
    def __init__(self, role_key, cfg):
        super().__init__(
            label=role_key.upper(),
            emoji=cfg["emoji"],
            style=discord.ButtonStyle.secondary,
            custom_id=f"signup:role:{role_key}",
        )
        self.role_key = role_key

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle(interaction, self.role_key)


class EventView(discord.ui.View):
    """Vista persistente con un botón por rol."""

    def __init__(self, manager, template_key, template=None):
        super().__init__(timeout=None)
        self.manager = manager
        self.template_key = template_key
        # `template` permite reconstruir la vista desde una plantilla resuelta
        # por servidor o desde la Composicion guardada del evento (Req 3, 15.2);
        # como fallback se usa la plantilla global del manager.
        if template is None:
            template = manager.get_template(template_key)
        for role_key, cfg in template["roles"].items():
            self.add_item(RoleButton(role_key, cfg))

    async def handle(self, interaction: discord.Interaction, role_key):
        message_id = interaction.message.id
        result, event = self.manager.toggle(
            message_id, role_key, interaction.user.id, interaction.user.display_name
        )

        if result == self.manager.NOT_FOUND:
            await interaction.response.send_message(
                "Este evento ya no está disponible.", ephemeral=True)
            return

        if result == self.manager.FULL:
            await interaction.response.send_message(
                f"El rol **{role_key.upper()}** ya está completo.", ephemeral=True)
            return

        # El embed se construye desde la Composicion guardada en el evento, de
        # modo que funciona igual para plantillas propias de un servidor que no
        # están en `templates.json` (Req 3, 15).
        template = {"roles": event.get("composition") or {}}
        complete = self.manager.is_complete(event)
        embed = build_embed(event, template, complete=complete)

        if complete:
            for item in self.children:
                item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

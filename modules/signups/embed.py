from discord import Embed


def build_embed(event, template, complete=False):
    """Construye el embed de un evento a partir de su estado y plantilla."""
    title = event["title"]
    roles_cfg = template["roles"]
    state = event["state"]

    if complete:
        embed = Embed(
            title=f"✅ ¡{title} completo!",
            description="El grupo está listo para comenzar.\n\n**Miembros del equipo:**",
            color=0x2ecc71,
        )
    else:
        embed = Embed(
            title=title,
            description="Pulsa un botón para apuntarte a un rol.",
            color=0x5865F2,
        )

    for role_key, cfg in roles_cfg.items():
        emoji = cfg["emoji"]
        plazas = cfg["plazas"]
        members = state.get(role_key, [])
        occupied = len(members)

        names = "\n".join(m["name"] for m in members) if members else "—"
        free = plazas - occupied
        if free > 0:
            names += ("\n" if members else "") + "\n".join("*(libre)*" for _ in range(free))

        embed.add_field(
            name=f"{emoji} {role_key.upper()} ({occupied}/{plazas})",
            value=names,
            inline=False,
        )

    if not complete:
        embed.set_footer(text="Pulsa de nuevo tu rol para salir.")
    return embed

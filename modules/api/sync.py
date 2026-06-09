"""Sincronización web → Discord de la API_Web (Req 9).

Cuando una Escritura_Autenticada cambia el Estado_Ocupacion de un Evento, la
API debe pedir a HubBot que reedite el mensaje del Evento en Discord para que
ambas caras (web y Discord) muestren el mismo estado (Req 9.1, 9.2).

La API corre en un hilo de Uvicorn separado del loop de `discord.py`. Por eso
la reedición del mensaje (una corrutina que toca la API de Discord) se programa
en el loop del bot con :func:`asyncio.run_coroutine_threadsafe`, sin bloquear la
respuesta HTTP. Cualquier fallo de sincronización se registra y se traga: nunca
debe romper la respuesta de la API ni revertir el cambio ya persistido
(Req 9.3).

Diseño de imports: este módulo NO importa `discord` ni los renderizadores de
`signups` a nivel de módulo, para que la app FastAPI siga siendo importable y
testeable sin el paquete `discord` instalado. `discord` y los renderizadores
solo se necesitan dentro de la corrutina ``_resync``, que únicamente se ejecuta
en el loop del bot real. Los tests inyectan dobles vía ``_get_renderers``.
"""
import asyncio
import logging

logger = logging.getLogger("mhub.api.sync")


def _get_renderers():
    """Devuelve ``(build_embed, EventView)`` del módulo signups.

    Import perezoso para no acoplar la app a `discord`: solo se llama dentro de
    la corrutina de reedición, que corre en el proceso real de HubBot. Los
    tests sustituyen esta función por una que devuelve dobles, evitando así
    importar `discord`.
    """
    from modules.signups.embed import build_embed
    from modules.signups.views import EventView
    return build_embed, EventView


def resync_event(bot, event_id):
    """Programa la reedición del mensaje del Evento en el loop del bot (Req 9.1).

    No bloquea esperando el resultado: devuelve el `Future` de
    `run_coroutine_threadsafe` (o ``None`` si no hay un bot/loop real). Un fallo
    al programar se registra y se traga, de modo que la sincronización nunca
    rompe la respuesta de la API (Req 9.3).
    """
    if bot is None:
        return None

    loop = getattr(bot, "loop", None)
    # En los tests la app se construye con un bot ligero cuyo `loop` no es un
    # loop de asyncio real (p. ej. `object()`); en ese caso no hay nada que
    # programar y evitamos crear una corrutina huérfana.
    if loop is None or not hasattr(loop, "call_soon_threadsafe"):
        return None

    coro = _resync(bot, event_id)
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:  # pragma: no cover - defensa ante loops inválidos
        # Cerramos la corrutina para no dejarla sin await y registramos el fallo.
        coro.close()
        logger.warning(
            "No se pudo programar la sincronización del evento %s: %s",
            event_id, exc)
        return None


async def _resync(bot, event_id):
    """Reedita el mensaje de Discord del Evento con su Estado_Ocupacion vigente.

    Localiza el canal y el mensaje (channel_id/message_id del evento),
    reconstruye el embed y la `EventView` desde la Composicion guardada y hace
    ``message.edit(...)`` (Req 9.2). Si no encuentra el canal/mensaje o falla la
    edición, conserva el cambio ya persistido y registra el fallo, sin propagar
    la excepción (Req 9.3).
    """
    manager = getattr(bot, "signup_manager", None)
    if manager is None:
        logger.warning("Sincronización omitida: no hay signup_manager.")
        return

    event = manager.get(event_id)
    if not event:
        logger.warning("Sincronización: evento %s no encontrado.", event_id)
        return

    channel_id = event.get("channel_id")
    message_id = event.get("message_id")

    channel = bot.get_channel(channel_id) if channel_id is not None else None
    if channel is None:
        # No se localiza el canal: el cambio queda persistido (Req 9.3).
        logger.warning(
            "Sincronización: canal %s no localizado para el evento %s; "
            "el cambio se conserva.", channel_id, event_id)
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception as exc:
        # discord.NotFound u otros errores al obtener el mensaje: se conserva el
        # cambio y se registra el fallo (Req 9.3).
        logger.warning(
            "Sincronización: no se pudo obtener el mensaje %s del evento %s "
            "(%s); el cambio se conserva.", message_id, event_id, exc)
        return

    if message is None:
        logger.warning(
            "Sincronización: mensaje %s no encontrado para el evento %s; "
            "el cambio se conserva.", message_id, event_id)
        return

    try:
        build_embed, EventView = _get_renderers()
        composition = event.get("composition") or {}
        template = {"roles": composition}
        complete = manager.is_complete(event)
        embed = build_embed(event, template, complete=complete)
        view = EventView(manager, event.get("template_key"), template=template)
        if complete:
            # Mismo comportamiento que la vista de Discord al completarse el
            # evento: se deshabilitan los botones.
            for item in getattr(view, "children", []):
                item.disabled = True
        await message.edit(embed=embed, view=view)
    except Exception as exc:
        # Cualquier fallo al reconstruir/editar se registra sin romper la API y
        # sin revertir el cambio persistido (Req 9.3).
        logger.warning(
            "Sincronización: no se pudo reeditar el mensaje %s del evento %s "
            "(%s); el cambio se conserva.", message_id, event_id, exc)
        return

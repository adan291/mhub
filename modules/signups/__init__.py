"""Módulo de inscripción a eventos con roles y cupos (ex-Mplux)."""
from core import config
from core.event_bus import EventBus
from core.guild_config_store import get_or_create_guild_config_store
from .store import EventStore
from .manager import EventManager
from .views import EventView
from .cog import SignupCog


async def setup(bot):
    templates = config.Config.load_json("templates.json")
    store = EventStore(config.Config.get_db_path("events.sqlite"), templates)
    # EventBus compartido: una ÚNICA instancia para que tanto las acciones de
    # Discord (vía EventManager) como la Escritura_Autenticada web publiquen los
    # cambios en el mismo bus, y el Canal_Tiempo_Real (SSE) de la API los reciba
    # (Req 10.1, 10.2). Se atribuye al bot para que `build_app` lo inyecte en
    # `app.state.event_bus` (vía getattr) y la API lo comparta.
    bus = getattr(bot, "event_bus", None)
    if bus is None:
        bus = EventBus()
        bot.event_bus = bus
    # El loop del bot existe en este punto; se fija como loop propietario por
    # defecto. El consumidor SSE de la API lo reajusta al loop que sirve su
    # petición (el de Uvicorn) al suscribirse, manteniendo la entrega segura
    # entre hilos.
    bot_loop = getattr(bot, "loop", None)
    if bot_loop is not None:
        bus.set_loop(bot_loop)
    manager = EventManager(store, templates, bus=bus)
    bot.signup_manager = manager
    # Store compartido de Configuracion_Por_Servidor: las plantillas de
    # `templates.json` son la configuración por defecto con fallback (Req 3.3).
    guild_config_store = get_or_create_guild_config_store(
        bot, default_templates=templates)
    await bot.add_cog(SignupCog(bot, manager, guild_config_store, bot.channel_id))


async def restore(bot):
    """Re-registra las vistas persistentes de los eventos guardados."""
    manager = bot.signup_manager
    for event in manager.store.all():
        try:
            # La vista se reconstruye desde la Composicion guardada en el propio
            # evento, de modo que funciona también para plantillas propias de un
            # servidor que no están en `templates.json` (Req 3, 15.2).
            view = EventView(
                manager, event["template_key"],
                template={"roles": event.get("composition") or {}})
            bot.add_view(view, message_id=event["message_id"])
        except Exception as e:
            print(f"No se pudo restaurar el evento {event['message_id']}: {e}")

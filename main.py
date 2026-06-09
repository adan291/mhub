import asyncio

from core import config
from core.bot import HubBot


async def main():
    config.Config.load_env()
    token = config.Config.get_token()
    if not token:
        raise ValueError("El token de Discord es inválido o no se cargó correctamente (DISCORD_TOKEN_HUB).")

    intents = config.Config.get_intents()
    channel_id = config.Config.get_channel_id()
    guild_id = config.Config.get_guild_id()

    bot = HubBot(intents=intents, channel_id=channel_id, guild_id=guild_id)

    try:
        await bot.start(token)
    except KeyboardInterrupt:
        print("El bot recibió una interrupción (Ctrl+C).")
    except asyncio.CancelledError:
        print("La tarea asincrónica fue cancelada.")
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")
    finally:
        print("Cerrando el bot...")
        await bot.close()
        print("Bot cerrado correctamente.")


if __name__ == "__main__":
    # HubBot y API_Web se sirven en el MISMO proceso (Req 14.3): el módulo
    # `api` (en ENABLED_MODULES) arranca Uvicorn en un hilo daemon durante
    # `setup(bot)`, conservando la ruta de salud `/` que Fly.io espera. Por eso
    # ya no se levanta el antiguo servidor Flask de `core/webserver.py`, que
    # competiría por el mismo PORT.
    asyncio.run(main())

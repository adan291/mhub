"""Módulo API_Web: sirve una API HTTP (FastAPI) dentro del proceso de HubBot.

Sigue el contrato de módulo `setup(bot)`/`restore(bot)` sin tocar el núcleo
(Req 5.3, 13.3, 13.4). `setup` construye la app con `build_app(bot)` y arranca
Uvicorn en un hilo daemon para no bloquear el loop del bot de Discord (Req 5.1,
5.4). El servidor escucha en `0.0.0.0` y en el puerto de la variable de entorno
`PORT` (por defecto 8000), conservando la ruta de salud que Fly.io espera.

`build_app` vive en `modules/api/app.py` y no importa `discord`, de modo que la
app es construible y testeable de forma aislada. Los imports de Uvicorn se hacen
dentro de `setup`, que solo se ejecuta dentro de HubBot.
"""
import os
import threading

from .app import build_app


async def setup(bot):
    """Construye la app y arranca Uvicorn en un hilo daemon (no bloqueante)."""
    import uvicorn

    app = build_app(bot)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    # `server.run()` crea su propio event loop en este hilo, separado del loop
    # del bot, por lo que no interfiere con Discord (Req 5.1, 5.4).
    thread = threading.Thread(target=server.run, daemon=True, name="mhub-api")
    thread.start()

    # Referencias para inspección/cierre desde el bot.
    bot.api_app = app
    bot.api_server = server
    bot.api_thread = thread


async def restore(bot):
    """No-op por ahora; reservado para re-suscripciones tras un reinicio."""
    return None

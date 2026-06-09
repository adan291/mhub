"""Fábrica de la aplicación FastAPI de la API_Web.

Este módulo es deliberadamente independiente de `discord`: `build_app` solo
necesita un objeto tipo "bot" del que leer referencias (managers, stores, loop
y, opcionalmente, el EventBus). Eso permite construir y testear la app con un
objeto ligero (p. ej. un `SimpleNamespace`) sin un bot real ni el paquete
`discord` instalado.

`build_app` inyecta esas referencias en `app.state` para que las tareas
posteriores (lectura pública, autenticación, escritura, sincronización y
tiempo real vía SSE) añadan sus endpoints reutilizando la MISMA fuente de
verdad que usa HubBot (Req 5.2, 13.1, 13.2).

Por ahora la app solo expone rutas de salud:
  - `GET /api/health`: salud de la API_Web.
  - `GET /`: equivalente de salud para compatibilidad con el health-check de
    Fly.io (sustituye al antiguo `core/webserver.py` de Flask).
"""
from fastapi import FastAPI


def _load_auth():
    """Importa el submódulo `auth` de forma robusta.

    Cuando `app.py` se importa como parte del paquete `modules.api`, el import
    relativo funciona. Pero los tests cargan `app.py` por ruta de archivo con
    importlib (sin contexto de paquete, para evitar `discord`), y ahí el import
    relativo falla; en ese caso cargamos `auth.py` por ruta junto a este fichero.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import auth  # type: ignore
        return auth
    except ImportError:
        import importlib.util
        import os
        import sys

        auth_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.py")
        spec = importlib.util.spec_from_file_location("mhub_api_auth", auth_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_auth"] = module
        spec.loader.exec_module(module)
        return module


def _load_read_routes():
    """Importa el submódulo `read_routes` de forma robusta.

    Igual que `_load_auth`: import relativo dentro del paquete `modules.api` y,
    en su defecto (carga por ruta en tests), carga `read_routes.py` por ruta.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import read_routes  # type: ignore
        return read_routes
    except ImportError:
        import importlib.util
        import os
        import sys

        rr_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "read_routes.py")
        spec = importlib.util.spec_from_file_location("mhub_api_read_routes", rr_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_read_routes"] = module
        spec.loader.exec_module(module)
        return module


def _load_write_routes():
    """Importa el submódulo `write_routes` de forma robusta.

    Igual que `_load_read_routes`: import relativo dentro del paquete
    `modules.api` y, en su defecto (carga por ruta en tests), carga
    `write_routes.py` por ruta.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import write_routes  # type: ignore
        return write_routes
    except ImportError:
        import importlib.util
        import os
        import sys

        wr_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "write_routes.py")
        spec = importlib.util.spec_from_file_location("mhub_api_write_routes", wr_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_write_routes"] = module
        spec.loader.exec_module(module)
        return module


def _load_stream():
    """Importa el submódulo `stream` (Canal_Tiempo_Real, SSE) de forma robusta.

    Igual que el resto de cargadores: import relativo dentro del paquete
    `modules.api` y, en su defecto (carga por ruta en tests), carga `stream.py`
    por ruta junto a este fichero.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import stream  # type: ignore
        return stream
    except ImportError:
        import importlib.util
        import os
        import sys

        st_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "stream.py")
        spec = importlib.util.spec_from_file_location("mhub_api_stream", st_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_stream"] = module
        spec.loader.exec_module(module)
        return module


def _load_security():
    """Importa el submódulo `security` (rate limiting + CORS) de forma robusta.

    Igual que el resto de cargadores: import relativo dentro del paquete
    `modules.api` y, en su defecto (carga por ruta en tests), carga
    `security.py` por ruta junto a este fichero.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import security  # type: ignore
        return security
    except ImportError:
        import importlib.util
        import os
        import sys

        sec_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "security.py")
        spec = importlib.util.spec_from_file_location("mhub_api_security", sec_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_security"] = module
        spec.loader.exec_module(module)
        return module


def build_app(bot):
    """Construye la app FastAPI inyectando las dependencias del `bot`.

    `bot` puede ser el HubBot real o cualquier objeto que exponga los
    atributos esperados (todos opcionales aquí, leídos con `getattr` para no
    acoplar el arranque al orden de carga de los módulos):
      - `signup_manager`   (EventManager)
      - `registry_store`   (RegistryStore)
      - `guild_config_store` (GuildConfigStore)
      - `loop`             (asyncio event loop propietario de las colas)
      - `event_bus`        (EventBus interno para el Canal_Tiempo_Real)
    """
    app = FastAPI(title="mhub API", version="0.1.0")

    # Inyección de la fuente de verdad compartida con el bot. Se guardan en
    # `app.state` para que los endpoints posteriores las consuman sin volver a
    # crear instancias (Req 5.2, 13.1, 13.2).
    # Referencia al propio bot: la necesita la sincronización web→Discord para
    # programar la reedición del mensaje en el loop del bot (Req 9). Se guarda
    # con `getattr`-like acceso directo; los tests usan un objeto ligero sin
    # loop real, y la sincronización está guardada para ser no-op en ese caso.
    app.state.bot = bot
    app.state.signup_manager = getattr(bot, "signup_manager", None)
    app.state.registry_store = getattr(bot, "registry_store", None)
    app.state.guild_config_store = getattr(bot, "guild_config_store", None)
    app.state.bridge_service = getattr(bot, "bridge_service", None)
    app.state.loop = getattr(bot, "loop", None)
    app.state.event_bus = getattr(bot, "event_bus", None)

    # Autenticación Discord OAuth2 y sesión por cookie firmada (Req 7). Se
    # registran /auth/login, /auth/callback, /auth/logout y /api/me, y se deja
    # en app.state el SessionManager y el cliente OAuth para las dependencias.
    auth = _load_auth()
    auth.register_auth_routes(app, config=auth.OAuthConfig.from_env())

    # Endpoints de Lectura_Publica (Req 6): eventos, directorio y sugerencias.
    # Se registran sobre la misma fuente de verdad inyectada en app.state.
    read_routes = _load_read_routes()
    read_routes.register_read_routes(app)

    # Endpoints de Escritura_Autenticada (Req 8): join/leave de eventos y alta/
    # borrado de entradas del directorio. Requieren Sesion_Web y reutilizan la
    # misma lógica de negocio (EventManager/RegistryStore). Se registran tras
    # auth/lectura.
    write_routes = _load_write_routes()
    write_routes.register_write_routes(app)

    # Canal_Tiempo_Real (Req 10): GET /api/stream emite por SSE los cambios de
    # Evento publicados en el EventBus, tanto los originados en Discord (vía
    # EventManager) como los de la Escritura_Autenticada web. Solo proyecta el
    # EventDTO público (sin user_id, Req 10.5 / Property 1).
    stream = _load_stream()
    stream.register_stream_routes(app)

    # Seguridad transversal (Req 12): rate limiting en memoria por IP/identidad
    # (incluido el caso límite=0 → 429 a todo) y CORS restringido a
    # FRONTEND_ORIGIN. La exigencia de Sesion_Web para escritura ya la cubren
    # los endpoints de write_routes vía get_current_identity (Req 12.2).
    security = _load_security()
    security.register_security(app)

    @app.get("/api/health")
    async def api_health():
        """Salud de la API_Web."""
        return {"status": "ok"}

    @app.get("/")
    async def root_health():
        """Salud equivalente para el health-check de Fly.io."""
        return {"status": "ok", "service": "mhub"}

    return app

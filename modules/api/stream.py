"""Canal de Tiempo_Real de la API_Web vía Server-Sent Events (Req 10).

`GET /api/stream` mantiene una conexión HTTP abierta (`text/event-stream`) y
emite un evento `event_changed` cada vez que un Evento cambia de estado, tanto
si el cambio procede de una acción en Discord (vía `EventManager`, que publica
en el `EventBus`) como de una Escritura_Autenticada de la web (Req 10.1, 10.2).

El endpoint se suscribe al `EventBus` compartido (`app.state.event_bus`) y, por
cada cambio publicado, recupera el estado VIGENTE del Evento desde la fuente de
verdad (`app.state.signup_manager.get(event_id)`) y lo proyecta al `EventDTO`
público de `dtos.py`. Ese DTO NUNCA contiene `user_id` ni tokens, de modo que
la notificación a clientes no autenticados respeta la invariante de no
exposición de identidad (Req 10.5, Property 1).

Recuperación de estado: no se garantiza el replay de los cambios perdidos; el
cliente, al (re)conectar, llama a `GET /api/events` para recuperar el estado
vigente (Req 10.4). Al desconectarse, su cola se da de baja del bus.

Reto de los dos loops: las colas de los suscriptores SSE viven en el loop que
sirve la petición (el de Uvicorn, en un hilo distinto al del bot). El
`EventManager` puede publicar desde el loop del bot; el `EventBus` programa la
entrega de forma segura entre hilos con `call_soon_threadsafe`. Por eso el
suscriptor se crea DENTRO de la corrutina de la petición y se fija el loop del
bus al loop que la sirve.
"""
import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse


def _load_dtos():
    """Importa el submódulo `dtos` de forma robusta.

    Dentro del paquete `modules.api` funciona el import relativo. Cuando
    `app.py`/`stream.py` se cargan por ruta (tests con importlib, sin contexto
    de paquete), se carga `dtos.py` por ruta junto a este fichero.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import dtos  # type: ignore
        return dtos
    except ImportError:
        import importlib.util
        import os
        import sys

        dtos_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "dtos.py")
        spec = importlib.util.spec_from_file_location("mhub_api_dtos", dtos_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_dtos"] = module
        spec.loader.exec_module(module)
        return module


def format_event_changed(event_dto: dict) -> str:
    """Formatea un `EventDTO` (dict) como un mensaje SSE `event_changed`.

    El formato sigue la especificación de Server-Sent Events: una línea
    ``event:`` con el nombre del evento, una línea ``data:`` con el cuerpo JSON
    y una línea en blanco que cierra el mensaje.
    """
    payload = json.dumps(event_dto, ensure_ascii=False)
    return f"event: event_changed\ndata: {payload}\n\n"


def render_change(change, manager, dtos) -> str | None:
    """Proyecta un cambio publicado en el bus a un mensaje SSE público.

    Recupera el estado vigente del Evento (`manager.get(event_id)`) y lo
    proyecta al `EventDTO` público (sin `user_id`, Req 10.5 / Property 1).
    Devuelve ``None`` cuando el cambio no es proyectable (sin `event_id`, sin
    manager, o el Evento ya no existe): en ese caso no se emite nada y el
    cliente recuperará el estado al reconsultar (Req 10.4).
    """
    if not change or manager is None:
        return None
    event_id = change.get("event_id")
    if event_id is None:
        return None
    event = manager.get(event_id)
    if not event:
        return None
    dto = dtos.event_to_dto(event).model_dump()
    return format_event_changed(dto)


async def event_stream(request: Request, *, keepalive_timeout: float = 15.0):
    """Generador del flujo SSE para una conexión concreta.

    Crea su propio suscriptor en el `EventBus` (su cola vive en el loop que
    sirve la petición) y, ante cada cambio, emite el `EventDTO` público. Al
    terminar (desconexión del cliente o cierre del servidor), da de baja la
    cola del bus (Req 10.4).

    Se define a nivel de módulo (y no como closure de `register_stream_routes`)
    para poder ejercerlo de forma determinista en los tests con un `request`
    falso que controle la desconexión, sin depender del streaming infinito a
    través del `TestClient` (que no entrega `http.disconnect` y provoca un
    deadlock al cerrar la conexión).
    """
    dtos = _load_dtos()
    bus = getattr(request.app.state, "event_bus", None)
    manager = getattr(request.app.state, "signup_manager", None)
    if bus is None:
        return

    # La cola de este suscriptor vive en el loop que sirve la petición
    # (el de Uvicorn). Fijamos ahí el loop del bus para que las
    # publicaciones desde el loop del bot lleguen vía call_soon_threadsafe.
    try:
        bus.set_loop(asyncio.get_running_loop())
    except Exception:
        pass

    queue = bus.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                change = await asyncio.wait_for(queue.get(), timeout=keepalive_timeout)
            except asyncio.TimeoutError:
                # Comentario SSE de keep-alive: mantiene viva la conexión y
                # permite detectar desconexiones en la siguiente iteración.
                yield ": keep-alive\n\n"
                continue
            message = render_change(change, manager, dtos)
            if message is not None:
                yield message
    finally:
        bus.unsubscribe(queue)


def register_stream_routes(app):
    """Monta el endpoint SSE de Tiempo_Real en la app."""

    @app.get("/api/stream")
    async def stream(request: Request):
        """Suscribe al cliente al canal de cambios en tiempo real (Req 10.1)."""
        return StreamingResponse(
            event_stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Desactiva el buffering de proxies inversos (p. ej. nginx).
                "X-Accel-Buffering": "no",
            },
        )

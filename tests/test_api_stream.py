"""Tests del Canal_Tiempo_Real (SSE) de la API_Web (Req 10, task 11).

Como en el resto de tests de la API, `modules/api/app.py` y sus submódulos se
cargan por ruta de archivo con importlib para evitar el `__init__.py` del
paquete `modules` (que importa `discord`, no instalado aquí). La capa de
dominio (EventStore/EventManager) y el EventBus se cargan vía
`tests/domain_loader.py`.

Estrategia (evitando la fragilidad del streaming real + hilos): se prueba el
canal en las capas deterministas y se añade una prueba de cableado con
`TestClient`:

  - `format_event_changed` / `render_change`: framing SSE correcto y proyección
    al EventDTO público SIN `user_id` (Req 10.5 / Property 1).
  - Emisión tras una "acción de Discord": `EventManager.toggle` publica en el
    bus compartido; el suscriptor recibe el cambio y se proyecta a SSE.
  - Emisión tras una Escritura_Autenticada web: un POST /join publica en el
    MISMO bus; el suscriptor lo recibe.
  - Ausencia de `user_id` en cualquier carga emitida.
  - Cableado del endpoint `GET /api/stream` (200 + `text/event-stream`).
"""
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import asyncio
import pytest
from fastapi.testclient import TestClient

from tests.domain_loader import EventBus, EventManager, EventStore

# Secreto de sesión determinista y cookies no-Secure para que el TestClient
# (http://testserver) almacene/reenvíe la cookie de sesión en el POST web.
os.environ.setdefault("SESSION_SECRET", "test-secret-please-change")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("DISCORD_CLIENT_ID", "client-123")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "secret-456")
os.environ.setdefault("OAUTH_REDIRECT_URI", "https://example.test/auth/callback")
os.environ.setdefault("FRONTEND_ORIGIN", "")


def _load_api_module(filename, modname):
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "modules", "api", filename,
    )
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


_app_mod = _load_api_module("app.py", "api_app_stream_under_test")
_stream_mod = _load_api_module("stream.py", "api_stream_under_test")
_dtos_mod = _load_api_module("dtos.py", "api_dtos_stream_under_test")
_auth_mod = _load_api_module("auth.py", "api_auth_stream_under_test")

build_app = _app_mod.build_app
format_event_changed = _stream_mod.format_event_changed
render_change = _stream_mod.render_change
event_stream = _stream_mod.event_stream
Identity = _auth_mod.Identity
SessionManager = _auth_mod.SessionManager
SESSION_COOKIE = _auth_mod.SESSION_COOKIE


class _FakeAppState:
    """`app.state` mínimo: expone event_bus y signup_manager."""

    def __init__(self, bus, manager):
        self.event_bus = bus
        self.signup_manager = manager


class _FakeApp:
    def __init__(self, bus, manager):
        self.state = _FakeAppState(bus, manager)


class _FakeRequest:
    """Request falso para ejercer `event_stream` sin `TestClient`.

    `disconnect_after` indica en qué llamada a `is_disconnected()` se reporta
    la desconexión del cliente. Esto permite probar la suscripción/baja del
    bus y la emisión de cambios de forma determinista, sin el deadlock del
    streaming infinito real a través del `TestClient`.
    """

    def __init__(self, bus, manager, disconnect_after=0):
        self.app = _FakeApp(bus, manager)
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self):
        result = self._calls >= self._disconnect_after
        self._calls += 1
        return result


def _contains_user_id(text: str) -> bool:
    """¿Aparece el substring 'user_id' en una carga SSE/JSON?"""
    return "user_id" in text.lower()


def _make_manager(tmp_path, bus):
    """EventManager real sobre SQLite temporal, con el bus compartido."""
    store = EventStore(str(tmp_path / "events.sqlite"), templates={})
    manager = EventManager(store, {}, bus=bus)
    composition = {
        "backend": {"emoji": "🔧", "plazas": 2},
        "frontend": {"emoji": "🎨", "plazas": 1},
    }
    manager.create(1001, 5001, 7001, "Equipo Alfa", composition)
    return manager


# --------------------------------------------------------------------------- #
# Capa pura: framing SSE y proyección al DTO público
# --------------------------------------------------------------------------- #
def test_format_event_changed_framing():
    msg = format_event_changed({"id": "1001", "title": "Equipo Alfa"})
    assert msg.startswith("event: event_changed\n")
    assert "\ndata: " in msg
    assert msg.endswith("\n\n")
    # La línea data: contiene JSON válido.
    data_line = msg.split("\ndata: ", 1)[1].rsplit("\n\n", 1)[0]
    assert json.loads(data_line) == {"id": "1001", "title": "Equipo Alfa"}


def test_render_change_projects_public_dto_without_user_id(tmp_path):
    bus = EventBus()
    manager = _make_manager(tmp_path, bus)
    manager.toggle(1001, "backend", 4242, "Alicia")  # inscribe a Alicia

    change = {"type": "event_changed", "event_id": 1001, "status": "activo"}
    msg = render_change(change, manager, _dtos_mod)

    assert msg is not None
    assert msg.startswith("event: event_changed\n")
    assert "Alicia" in msg            # nombre mostrado SÍ
    assert not _contains_user_id(msg)  # user_id NO (Req 10.5 / Property 1)
    # El id 4242 del usuario no debe filtrarse en la carga.
    assert "4242" not in msg


def test_render_change_returns_none_for_missing_event(tmp_path):
    bus = EventBus()
    manager = _make_manager(tmp_path, bus)
    assert render_change({"event_id": 999999}, manager, _dtos_mod) is None
    assert render_change({}, manager, _dtos_mod) is None
    assert render_change(None, manager, _dtos_mod) is None


# --------------------------------------------------------------------------- #
# Emisión tras una acción de Discord (EventManager.toggle → bus compartido)
# --------------------------------------------------------------------------- #
def test_emission_after_discord_action(tmp_path):
    """Una acción de Discord (toggle) publica en el bus y el suscriptor SSE la
    recibe y la proyecta sin `user_id` (Req 10.1, 10.5)."""
    bus = EventBus()  # sin loop → entrega directa, determinista
    manager = _make_manager(tmp_path, bus)

    queue = bus.subscribe()
    manager.toggle(1001, "backend", 4242, "Alicia")  # acción "de Discord"

    assert not queue.empty()
    change = queue.get_nowait()
    assert change["type"] == "event_changed"
    assert change["event_id"] == 1001

    msg = render_change(change, manager, _dtos_mod)
    assert msg is not None
    assert "Alicia" in msg
    assert not _contains_user_id(msg)


# --------------------------------------------------------------------------- #
# Emisión tras una Escritura_Autenticada web (POST /join → mismo bus)
# --------------------------------------------------------------------------- #
def test_emission_after_web_write(tmp_path):
    """Un POST /join autenticado publica en el MISMO bus compartido; el
    suscriptor lo recibe y la carga proyectada no contiene `user_id`."""
    bus = EventBus()  # sin loop → la publicación durante el POST entrega directa
    manager = _make_manager(tmp_path, bus)

    bot = SimpleNamespace(
        signup_manager=manager,
        registry_store=None,
        guild_config_store=None,
        bridge_service=None,
        loop=None,
        event_bus=bus,
    )
    app = build_app(bot)

    # Sesión web firmada con el MISMO SessionManager de la app.
    sm: SessionManager = app.state.session_manager
    token = sm.serialize(Identity(user_id="4242", username="Alicia", avatar=None))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)

    queue = bus.subscribe()
    resp = client.post("/api/events/1001/join", json={"role": "backend"})
    assert resp.status_code == 200
    # La respuesta del endpoint tampoco expone user_id.
    assert not _contains_user_id(resp.text)

    # El bus recibió al menos un event_changed para el evento 1001.
    changes = []
    while not queue.empty():
        changes.append(queue.get_nowait())
    assert any(
        c.get("type") == "event_changed" and c.get("event_id") == 1001
        for c in changes
    )

    msg = render_change(changes[0], manager, _dtos_mod)
    assert msg is not None
    assert "Alicia" in msg
    assert not _contains_user_id(msg)


# --------------------------------------------------------------------------- #
# Cableado del endpoint SSE
# --------------------------------------------------------------------------- #
def test_stream_endpoint_is_wired(tmp_path):
    """`GET /api/stream` devuelve un `StreamingResponse` con
    `media_type=text/event-stream` y las cabeceras de no-buffering (Req 10.1).

    NOTA: no se abre una conexión SSE real con `client.stream`. El endpoint es
    un generador infinito (`while True`) que solo termina cuando el cliente se
    desconecta; con `TestClient` (síncrono) el `http.disconnect` no se procesa
    si el cuerpo del stream no se consume, dejando el generador colgado en el
    teardown. En su lugar invocamos el handler directamente: construir el
    `StreamingResponse` NO ejecuta el generador (solo se crea el objeto), así
    que la verificación del cableado es determinista y no se cuelga.
    """
    import asyncio

    from starlette.requests import Request

    bus = EventBus()
    manager = _make_manager(tmp_path, bus)
    bot = SimpleNamespace(
        signup_manager=manager,
        registry_store=None,
        guild_config_store=None,
        bridge_service=None,
        loop=None,
        event_bus=bus,
    )
    app = build_app(bot)

    endpoint = None
    for route in app.routes:
        if getattr(route, "path", None) == "/api/stream":
            endpoint = route.endpoint
            break
    assert endpoint is not None, "El endpoint /api/stream no está registrado"

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/stream",
        "headers": [],
        "query_string": b"",
        "app": app,
    }
    request = Request(scope, receive=lambda: None)
    response = asyncio.run(endpoint(request))

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    # El generador no se ha iterado, por lo que no hay suscriptores en el bus.
    assert bus.subscriber_count == 0


def test_stream_unsubscribes_on_disconnect(tmp_path):
    """Al desconectarse el cliente, el suscriptor se da de baja del bus (Req 10.4).

    Ejerce `event_stream` directamente con un request falso que reporta la
    desconexión en la primera comprobación: el generador se suscribe, detecta
    la desconexión, sale del bucle y libera su cola en el `finally`.
    """
    bus = EventBus()
    manager = _make_manager(tmp_path, bus)
    request = _FakeRequest(bus, manager, disconnect_after=0)

    async def drain():
        async for _chunk in event_stream(request, keepalive_timeout=0.01):
            break  # no debería emitir nada: se desconecta de inmediato

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(drain())
    finally:
        loop.close()

    # Se suscribió y se dio de baja: el bus queda sin suscriptores.
    assert bus.subscriber_count == 0


def test_stream_emits_change_then_unsubscribes(tmp_path):
    """El generador emite un `event_changed` cuando hay un cambio en la cola y
    se da de baja al desconectarse (Req 10.1, 10.4), sin `user_id` (Req 10.5)."""
    bus = EventBus()  # sin loop → event_stream fija el loop en ejecución
    manager = _make_manager(tmp_path, bus)
    # 1ª comprobación viva (lee el cambio encolado), 2ª reporta desconexión.
    request = _FakeRequest(bus, manager, disconnect_after=1)

    async def collect():
        out = []

        async def consume():
            # keepalive alto: no debe emitir keep-alive antes de publicar.
            async for chunk in event_stream(request, keepalive_timeout=5.0):
                out.append(chunk)

        task = asyncio.create_task(consume())
        # Deja que el generador se suscriba y quede esperando en queue.get().
        await asyncio.sleep(0.05)
        manager.toggle(1001, "backend", 4242, "Alicia")  # publica en el bus
        await task
        return out

    loop = asyncio.new_event_loop()
    try:
        chunks = loop.run_until_complete(collect())
    finally:
        loop.close()

    body = "".join(chunks)
    assert "event: event_changed" in body
    assert "Alicia" in body
    assert not _contains_user_id(body)
    assert bus.subscriber_count == 0


def test_stream_route_registered(tmp_path):
    bus = EventBus()
    manager = _make_manager(tmp_path, bus)
    bot = SimpleNamespace(signup_manager=manager, event_bus=bus, loop=None)
    app = build_app(bot)
    paths = {route.path for route in app.routes}
    assert "/api/stream" in paths

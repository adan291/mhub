"""Tests de los endpoints de Escritura_Autenticada de la API_Web (Req 8, task 9).

Como en `test_api_read.py`/`test_api_auth.py`, `build_app` se carga por ruta de
archivo con importlib para evitar el `__init__.py` del paquete `modules` (que
importa `discord`). La capa de dominio se carga vía `tests/domain_loader.py`.

Se construyen stores reales sobre SQLite temporal, se inyectan en un bot ligero
(`SimpleNamespace`) y se autentica fijando una cookie de sesión firmada con el
MISMO `SessionManager` que usa la app (patrón de `test_api_auth.py`).

Cubre:
  - join (200 + ocupación reflejada) y leave (Req 8.1, 8.2).
  - rol completo → 409 (Req 8.6).
  - alta de entrada asociada al `user_id` de la sesión (Req 8.3).
  - borrado propio (204) vs ajeno (403, entrada conservada) (Req 8.4, 8.5).
  - la identidad procede de la sesión: un `user_id` del cuerpo se ignora
    (Req 7.4, 8.1, 8.3).
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.domain_loader import EventManager, EventStore, RegistryStore

# Secreto determinista y cookies no-Secure para que el TestClient las reenvíe.
os.environ.setdefault("SESSION_SECRET", "test-secret-please-change")
os.environ["COOKIE_SECURE"] = "0"


def _load_module(filename, modname):
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "modules", "api", filename,
    )
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


_app_mod = _load_module("app.py", "api_app_write_under_test")
_auth_mod = _load_module("auth.py", "api_auth_write_under_test")
build_app = _app_mod.build_app
Identity = _auth_mod.Identity
SESSION_COOKIE = _auth_mod.SESSION_COOKIE


def _contains_user_id(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if "user_id" in str(key).lower():
                return True
            if _contains_user_id(value):
                return True
        return False
    if isinstance(obj, (list, tuple)):
        return any(_contains_user_id(item) for item in obj)
    return False


@pytest.fixture
def bot(tmp_path):
    events_db = os.path.join(str(tmp_path), "events.sqlite")
    registry_db = os.path.join(str(tmp_path), "registry.sqlite")

    event_store = EventStore(events_db, templates={})
    manager = EventManager(event_store, templates={})
    registry_store = RegistryStore(registry_db)

    # Evento con un rol de 2 plazas y otro de 1 plaza (para probar "completo").
    manager.create(
        2001, 5001, 7001, "Equipo Web",
        {"backend": {"emoji": None, "plazas": 2}, "lead": {"emoji": None, "plazas": 1}},
    )

    return SimpleNamespace(
        signup_manager=manager,
        registry_store=registry_store,
        guild_config_store=None,
        loop=object(),
        event_bus=None,
    )


@pytest.fixture
def app(bot):
    return build_app(bot)


def _client_as(app, user_id, username=None):
    """TestClient autenticado como `user_id` mediante cookie de sesión firmada."""
    sm = app.state.session_manager
    token = sm.serialize(Identity(user_id=str(user_id), username=username))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


# --------------------------------------------------------------------------- #
# Autenticación obligatoria (Req 7.3 / 12.2)
# --------------------------------------------------------------------------- #
def test_join_without_session_returns_401(app):
    client = TestClient(app)
    resp = client.post("/api/events/2001/join", json={"role": "backend"})
    assert resp.status_code == 401


def test_register_without_session_returns_401(app):
    client = TestClient(app)
    resp = client.post(
        "/api/registry",
        json={"provider": "X", "category": "c", "name": "n", "link": "l"},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Join / leave (Req 8.1, 8.2)
# --------------------------------------------------------------------------- #
def test_join_reflects_occupancy(app):
    client = _client_as(app, "42", "Alicia")
    resp = client.post("/api/events/2001/join", json={"role": "backend"})
    assert resp.status_code == 200
    event = resp.json()
    roles = {r["role"]: r for r in event["roles"]}
    assert roles["backend"]["occupied"] == 1
    assert roles["backend"]["free"] == 1
    assert roles["backend"]["members"] == ["Alicia"]
    assert not _contains_user_id(event)


def test_leave_removes_user(app):
    client = _client_as(app, "42", "Alicia")
    client.post("/api/events/2001/join", json={"role": "backend"})

    resp = client.post("/api/events/2001/leave")
    assert resp.status_code == 200
    event = resp.json()
    roles = {r["role"]: r for r in event["roles"]}
    assert roles["backend"]["occupied"] == 0
    assert roles["backend"]["members"] == []


def test_leave_when_not_enrolled_is_idempotent(app):
    client = _client_as(app, "42", "Alicia")
    resp = client.post("/api/events/2001/leave")
    assert resp.status_code == 200
    event = resp.json()
    roles = {r["role"]: r for r in event["roles"]}
    assert roles["backend"]["occupied"] == 0


def test_join_nonexistent_event_returns_404(app):
    client = _client_as(app, "42", "Alicia")
    resp = client.post("/api/events/999999/join", json={"role": "backend"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Rol completo → 409 (Req 8.6)
# --------------------------------------------------------------------------- #
def test_join_full_role_returns_409(app):
    # "lead" tiene 1 plaza. El primer usuario la ocupa; el segundo recibe 409.
    _client_as(app, "1", "Uno").post("/api/events/2001/join", json={"role": "lead"})
    resp = _client_as(app, "2", "Dos").post(
        "/api/events/2001/join", json={"role": "lead"})
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Alta de entrada asociada a la sesión (Req 8.3) + identidad de la sesión
# --------------------------------------------------------------------------- #
def test_register_entry_owned_by_session_user(app):
    client = _client_as(app, "4242", "Alicia")
    # Incluimos un `user_id` falso en el cuerpo para demostrar que se IGNORA:
    # la entrada debe quedar asociada al user_id de la sesión (4242), no al 999.
    resp = client.post(
        "/api/registry",
        json={
            "provider": "Alicia", "category": "backend", "name": "Python",
            "link": "https://a.example", "user_id": "999",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert not _contains_user_id(body)

    # La entrada quedó asociada al user_id de la sesión, no al del cuerpo.
    rows = app.state.registry_store.all_entries()
    assert len(rows) == 1
    assert str(rows[0]["user_id"]) == "4242"
    assert rows[0]["name"] == "Python"


# --------------------------------------------------------------------------- #
# Borrado propio (204) vs ajeno (403, conservado) (Req 8.4, 8.5)
# --------------------------------------------------------------------------- #
def test_delete_own_entry(app):
    store = app.state.registry_store
    store.add("Alicia", "backend", "Python", "https://a.example", 4242)
    entry_id = store.all_entries()[0]["id"]

    client = _client_as(app, "4242", "Alicia")
    resp = client.request("DELETE", f"/api/registry/{entry_id}")
    assert resp.status_code == 204
    assert store.all_entries() == []


def test_delete_other_users_entry_is_forbidden_and_preserved(app):
    store = app.state.registry_store
    store.add("Bruno", "backend", "Go", "https://b.example", 9999)
    entry_id = store.all_entries()[0]["id"]

    # Sesión de otro usuario (4242) intenta borrar la entrada de 9999.
    client = _client_as(app, "4242", "Alicia")
    resp = client.request("DELETE", f"/api/registry/{entry_id}")
    assert resp.status_code == 403
    # La entrada se conserva intacta (Req 8.5).
    rows = store.all_entries()
    assert len(rows) == 1
    assert str(rows[0]["user_id"]) == "9999"


def test_delete_nonexistent_entry_returns_404(app):
    client = _client_as(app, "4242", "Alicia")
    resp = client.request("DELETE", "/api/registry/123456")
    assert resp.status_code == 404

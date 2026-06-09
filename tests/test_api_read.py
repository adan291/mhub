"""Tests de los endpoints de Lectura_Publica de la API_Web (Req 6, task 7).

Como en `test_api_app.py`/`test_api_auth.py`, `build_app` se carga por ruta de
archivo con importlib para evitar el `__init__.py` del paquete `modules` (que
importa `discord`). La capa de dominio (EventStore/EventManager, RegistryStore,
BridgeService) se carga vía `tests/domain_loader.py`, que también evita
`discord`.

Estos tests construyen stores reales sobre SQLite temporal, los inyectan en un
bot ligero (`SimpleNamespace`) y verifican con `TestClient`:
  - Contenido correcto de cada endpoint (Req 6.1, 6.2, 11.4, 4).
  - Que NINGUNA respuesta pública contiene `user_id` (Req 6.3).
  - Que la Lectura_Publica no requiere sesión (Req 6.4).
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.domain_loader import (
    BridgeService,
    EventManager,
    EventStore,
    RegistryStore,
)


def _load_app_module():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "modules", "api", "app.py",
    )
    spec = importlib.util.spec_from_file_location("api_app_read_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["api_app_read_under_test"] = module
    spec.loader.exec_module(module)
    return module


build_app = _load_app_module().build_app


def _contains_user_id(obj):
    """Escaneo recursivo: ¿aparece la clave/substring 'user_id' en `obj`?"""
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
def populated_bot(tmp_path):
    """Bot ligero con stores reales poblados con datos de ejemplo."""
    events_db = os.path.join(str(tmp_path), "events.sqlite")
    registry_db = os.path.join(str(tmp_path), "registry.sqlite")

    event_store = EventStore(events_db, templates={})
    manager = EventManager(event_store, templates={})
    registry_store = RegistryStore(registry_db)
    bridge = BridgeService(registry_store, event_store)

    # Evento activo con dos roles y un miembro inscrito.
    composition = {
        "backend": {"emoji": "🔧", "plazas": 2},
        "frontend": {"emoji": "🎨", "plazas": 1},
    }
    manager.create(1001, 5001, 7001, "Equipo Alfa", composition)
    manager.toggle(1001, "backend", 4242, "Alicia")

    # Evento archivado (historial).
    manager.create(1002, 5001, 7001, "Equipo Beta", {"qa": {"emoji": None, "plazas": 1}})
    manager.archive(1002)

    # Directorio: dos personas con la skill "backend".
    registry_store.add("Alicia", "backend", "Python", "https://a.example", 4242)
    registry_store.add("Bruno", "backend", "Go", "https://b.example", 9999)

    bot = SimpleNamespace(
        signup_manager=manager,
        registry_store=registry_store,
        bridge_service=bridge,
        guild_config_store=None,
        loop=object(),
        event_bus=None,
    )
    return bot


@pytest.fixture
def client(populated_bot):
    return TestClient(build_app(populated_bot))


def test_list_events_returns_active_with_occupancy(client):
    resp = client.get("/api/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    event = events[0]
    assert event["title"] == "Equipo Alfa"
    assert event["status"] == "activo"
    roles = {r["role"]: r for r in event["roles"]}
    assert roles["backend"]["capacity"] == 2
    assert roles["backend"]["occupied"] == 1
    assert roles["backend"]["free"] == 1
    assert roles["backend"]["members"] == ["Alicia"]
    assert roles["frontend"]["occupied"] == 0
    assert roles["frontend"]["free"] == 1
    assert not _contains_user_id(events)


def test_get_event_detail(client):
    resp = client.get("/api/events/1001")
    assert resp.status_code == 200
    event = resp.json()
    assert event["id"] == "1001"
    assert event["title"] == "Equipo Alfa"
    assert not _contains_user_id(event)


def test_get_event_not_found(client):
    resp = client.get("/api/events/999999")
    assert resp.status_code == 404


def test_list_archived_events(client):
    resp = client.get("/api/events/archived")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["title"] == "Equipo Beta"
    assert events[0]["status"] == "archivado"
    assert not _contains_user_id(events)


def test_list_registry(client):
    resp = client.get("/api/registry")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 2
    by_name = {e["name"]: e for e in entries}
    assert by_name["Python"]["category"] == "backend"
    assert by_name["Python"]["link"] == "https://a.example"
    assert "Alicia" in by_name["Python"]["providers"]
    assert not _contains_user_id(entries)


def test_search_registry(client):
    resp = client.get("/api/registry/search", params={"categoria": "backend", "q": "Go"})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["name"] == "Go"
    assert entries[0]["category"] == "backend"
    assert not _contains_user_id(entries)


def test_bridge_suggestions_excludes_enrolled_and_user_id(client):
    resp = client.get("/api/bridge/events/1001/suggestions")
    assert resp.status_code == 200
    suggestions = resp.json()
    by_role = {s["role"]: s for s in suggestions}
    # backend tiene a Bruno como candidato; Alicia (ya inscrita) queda excluida.
    backend_candidates = by_role["backend"]["candidates"]
    providers = {c["provider"] for c in backend_candidates}
    assert providers == {"Bruno"}
    # frontend no tiene candidatos.
    assert by_role["frontend"]["candidates"] == []
    assert not _contains_user_id(suggestions)


def test_public_reads_require_no_session(client):
    # Sin cookie de sesión, todos los endpoints de lectura responden 200.
    for path in ("/api/events", "/api/events/archived", "/api/registry"):
        assert client.get(path).status_code == 200

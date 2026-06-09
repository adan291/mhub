"""Test de propiedad (property-based) de no exposición de identidad (task 7.1).

Cubre la **Property 1** del diseño (design.md → "Correctness Properties"):

    Ninguna respuesta de Lectura_Publica ni notificación SSE a clientes no
    autenticados contiene `user_id` ni tokens de sesión.
    **Validates: Requirements 6.3, 12.6**

Estrategia: con Hypothesis generamos datos de dominio ARBITRARIOS (eventos con
miembros inscritos, entradas del directorio), donde cada miembro y cada entrada
llevan un `user_id` de Discord. Poblamos stores reales sobre SQLite temporal,
construimos la app FastAPI e interrogamos TODOS los endpoints de Lectura_Publica.
Para cada respuesta verificamos dos invariantes complementarios:

  1. Ninguna clave del JSON contiene la subcadena ``user_id`` (escaneo recursivo).
  2. Ningún valor de `user_id` realmente usado aparece, como texto, en el cuerpo
     serializado de la respuesta (detecta fugas aunque el valor se expusiera bajo
     otra clave o concatenado).

Para que el punto (2) no produzca falsos positivos, los `user_id` se generan como
enteros grandes y únicos, y los textos visibles (títulos, nombres, proveedores,
enlaces) se generan SOLO con letras, de modo que un `user_id` (dígitos) no puede
aparecer por coincidencia en un campo de texto legítimo.

Como en `test_api_read.py`, `build_app` se carga por ruta de archivo con
importlib para evitar el `__init__.py` del paquete `modules` (que importa
`discord`), y la capa de dominio se carga vía `tests/domain_loader.py`.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

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
    spec = importlib.util.spec_from_file_location("api_app_read_pbt_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["api_app_read_pbt_under_test"] = module
    spec.loader.exec_module(module)
    return module


build_app = _load_app_module().build_app


# --------------------------------------------------------------------------- #
# Detección de fugas
# --------------------------------------------------------------------------- #
def _has_user_id_key(obj):
    """Escaneo recursivo: ¿aparece la subcadena 'user_id' en alguna clave?"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if "user_id" in str(key).lower():
                return True
            if _has_user_id_key(value):
                return True
        return False
    if isinstance(obj, (list, tuple)):
        return any(_has_user_id_key(item) for item in obj)
    return False


# --------------------------------------------------------------------------- #
# Estrategias
# --------------------------------------------------------------------------- #
ROLE_KEYS = ["backend", "frontend", "qa", "devops", "design"]

# Texto visible SOLO con letras: así un user_id (dígitos) nunca colisiona por
# coincidencia con un nombre/proveedor/título/enlace legítimo.
_letters = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   min_size=1, max_size=12)

# `user_id` grandes y únicos por escenario para poder buscarlos como texto.
_user_ids = st.integers(min_value=10**9, max_value=10**12)


@st.composite
def compositions(draw):
    keys = draw(st.lists(
        st.sampled_from(ROLE_KEYS), min_size=1, max_size=len(ROLE_KEYS),
        unique=True))
    composition = {}
    for key in keys:
        composition[key] = {
            "emoji": draw(st.one_of(st.none(), st.sampled_from(["A", "B", "C"]))),
            "plazas": draw(st.integers(min_value=1, max_value=3)),
        }
    return composition


@st.composite
def events(draw):
    """Genera (composición, lista de inscripciones (user_id, role, name))."""
    composition = draw(compositions())
    role_keys = list(composition.keys())
    members = draw(st.lists(
        st.tuples(_user_ids, st.sampled_from(role_keys), _letters),
        min_size=0, max_size=8, unique_by=lambda t: t[0]))
    return composition, members


@st.composite
def registry_entries(draw):
    """Genera entradas del directorio: (provider, category, name, link, user_id)."""
    return draw(st.lists(
        st.tuples(
            _letters,                                   # provider
            st.sampled_from(ROLE_KEYS),                 # category
            _letters,                                   # name
            _letters.map(lambda s: f"https://{s}.example"),  # link
            _user_ids,                                  # user_id
        ),
        min_size=0, max_size=8))


@st.composite
def scenarios(draw):
    n_events = draw(st.integers(min_value=1, max_value=3))
    evs = [draw(events()) for _ in range(n_events)]
    archived = draw(st.lists(st.booleans(), min_size=n_events, max_size=n_events))
    return evs, archived, draw(registry_entries())


# --------------------------------------------------------------------------- #
# Property 1: no exposición de identidad en Lectura_Publica
# --------------------------------------------------------------------------- #
@settings(max_examples=10, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(scenarios())
def test_public_reads_never_expose_user_id_or_tokens(scenario):
    """Ninguna respuesta de Lectura_Publica expone `user_id` ni tokens.

    **Validates: Requirements 6.3, 12.6**
    """
    evs, archived_flags, entries = scenario

    tmpdir = tempfile.mkdtemp(prefix="mhub_read_pbt_")
    used_user_ids = set()
    try:
        events_db = os.path.join(tmpdir, "events.sqlite")
        registry_db = os.path.join(tmpdir, "registry.sqlite")

        event_store = EventStore(events_db, templates={})
        manager = EventManager(event_store, templates={})
        registry_store = RegistryStore(registry_db)
        bridge = BridgeService(registry_store, event_store)

        message_id = 1000
        event_ids = []
        for (composition, members), archived in zip(evs, archived_flags):
            message_id += 1
            manager.create(message_id, 5000, 7000, "Equipo", composition)
            for user_id, role_key, name in members:
                used_user_ids.add(user_id)
                manager.toggle(message_id, role_key, user_id, name)
            if archived:
                manager.archive(message_id)
            event_ids.append(message_id)

        for provider, category, name, link, user_id in entries:
            used_user_ids.add(user_id)
            registry_store.add(provider, category, name, link, user_id)

        bot = SimpleNamespace(
            signup_manager=manager,
            registry_store=registry_store,
            bridge_service=bridge,
            guild_config_store=None,
            loop=object(),
            event_bus=None,
        )
        client = TestClient(build_app(bot))

        paths = [
            "/api/events",
            "/api/events/archived",
            "/api/registry",
            "/api/registry/search?categoria=backend&q=",
        ]
        for eid in event_ids:
            paths.append(f"/api/events/{eid}")
            paths.append(f"/api/bridge/events/{eid}/suggestions")

        user_id_strings = {str(uid) for uid in used_user_ids}

        for path in paths:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} → {resp.status_code}"

            payload = resp.json()
            # Invariante 1: ninguna clave 'user_id' en la estructura.
            assert not _has_user_id_key(payload), (
                f"{path} expone una clave user_id: {payload}")

            # Invariante 2: ningún valor de user_id aparece como texto.
            body_text = json.dumps(payload)
            for uid_str in user_id_strings:
                assert uid_str not in body_text, (
                    f"{path} filtra el valor user_id {uid_str}: {body_text}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

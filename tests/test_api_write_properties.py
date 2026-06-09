"""Tests de propiedad (property-based) de Escritura_Autenticada (task 9.1).

Cubren dos invariantes del diseño (design.md → "Correctness Properties"):

  - **Property 4 — Identidad desde la sesión**: el `user_id` aplicado en una
    escritura procede SIEMPRE de la Sesion_Web, nunca del cuerpo de la petición.
    **Validates: Requirements 7.4, 12.3**

  - **Property 5 — Propiedad de propietario**: una entrada del directorio solo
    puede borrarla la Identidad propietaria; cualquier otro intento devuelve 403
    y deja la entrada intacta.
    **Validates: Requirements 8.5, 12.3**

Estrategia (igual que `test_api_read_properties.py` y `test_signups_properties.py`):
con Hypothesis generamos identidades de sesión y cuerpos de petición aleatorios,
poblamos stores reales sobre SQLite temporal (DB fresca por ejemplo, limpiada en
`finally`), construimos la app FastAPI cargada por ruta de archivo (para evitar el
`__init__.py` del paquete `modules`, que importa `discord`) y autenticamos fijando
una cookie de sesión firmada con el MISMO `SessionManager` que usa la app.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

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


_app_mod = _load_module("app.py", "api_app_write_pbt_under_test")
_auth_mod = _load_module("auth.py", "api_auth_write_pbt_under_test")
build_app = _app_mod.build_app
Identity = _auth_mod.Identity
SESSION_COOKIE = _auth_mod.SESSION_COOKIE


def _client_as(app, user_id, username=None):
    """TestClient autenticado como `user_id` mediante cookie de sesión firmada."""
    sm = app.state.session_manager
    token = sm.serialize(Identity(user_id=str(user_id), username=username))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


# --------------------------------------------------------------------------- #
# Estrategias
# --------------------------------------------------------------------------- #
# Texto visible SOLO con letras (sin dígitos), para que no colisione por
# coincidencia con un user_id (que son dígitos) al comparar propietarios.
_letters = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    min_size=1, max_size=12)

# user_id grandes y únicos para distinguir sesión vs cuerpo sin colisiones.
_user_ids = st.integers(min_value=10**9, max_value=10**12)

ROLE_KEYS = ["backend", "frontend", "qa", "devops", "design"]


def _make_stores(tmpdir):
    events_db = os.path.join(tmpdir, "events.sqlite")
    registry_db = os.path.join(tmpdir, "registry.sqlite")
    event_store = EventStore(events_db, templates={})
    manager = EventManager(event_store, templates={})
    registry_store = RegistryStore(registry_db)
    return manager, registry_store


def _make_bot(manager, registry_store):
    return SimpleNamespace(
        signup_manager=manager,
        registry_store=registry_store,
        guild_config_store=None,
        loop=object(),
        event_bus=None,
    )


# --------------------------------------------------------------------------- #
# Property 4: la identidad de toda escritura procede de la sesión
# --------------------------------------------------------------------------- #
@settings(max_examples=10, deadline=None,
          suppress_health_check=[HealthCheck.too_slow,
                                 HealthCheck.function_scoped_fixture])
@given(
    session_user_id=_user_ids,
    body_user_id=_user_ids,
    role=st.sampled_from(ROLE_KEYS),
    provider=_letters,
    category=st.sampled_from(ROLE_KEYS),
    name=_letters,
    link_seed=_letters,
)
def test_writes_use_session_identity_never_body(
        session_user_id, body_user_id, role, provider, category, name, link_seed):
    """El user_id aplicado en join y alta procede de la sesión, no del cuerpo.

    **Validates: Requirements 7.4, 12.3** (Property 4)
    """
    # La sesión y el user_id falso del cuerpo deben ser distintos para que la
    # comprobación sea significativa.
    session_uid = str(session_user_id)
    bogus_uid = str(body_user_id if body_user_id != session_user_id
                    else body_user_id + 1)

    tmpdir = tempfile.mkdtemp(prefix="mhub_write_pbt4_")
    try:
        manager, registry_store = _make_stores(tmpdir)
        # Composición con todos los roles disponibles (plazas amplias).
        composition = {key: {"emoji": None, "plazas": 5} for key in ROLE_KEYS}
        manager.create(3001, 5001, 7001, "Equipo", composition)

        app = build_app(_make_bot(manager, registry_store))
        client = _client_as(app, session_uid, "SesionUser")

        # --- join: el inscrito debe ser la identidad de la sesión ----------- #
        resp = client.post(
            "/api/events/3001/join",
            json={"role": role, "user_id": bogus_uid},
        )
        assert resp.status_code == 200, resp.text

        event = manager.get(3001)
        members = event["state"][role]
        member_ids = {str(m["id"]) for m in members}
        assert member_ids == {session_uid}, (
            f"join asoció ids {member_ids}, esperado solo {session_uid}")
        assert bogus_uid not in member_ids

        # --- alta de directorio: el propietario debe ser la sesión ---------- #
        link = f"https://{link_seed}.example"
        resp = client.post(
            "/api/registry",
            json={
                "provider": provider, "category": category, "name": name,
                "link": link, "user_id": bogus_uid,
            },
        )
        assert resp.status_code in (200, 201), resp.text

        rows = registry_store.all_entries()
        assert len(rows) == 1
        owner = str(rows[0]["user_id"])
        assert owner == session_uid, (
            f"entrada asociada a {owner}, esperado {session_uid} (cuerpo: {bogus_uid})")
        assert owner != bogus_uid
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Property 5: una entrada solo la borra su propietario
# --------------------------------------------------------------------------- #
@st.composite
def _ownership_scenario(draw):
    """Genera entradas de directorio con propietarios variados y una secuencia
    de intentos de borrado (entry_index, actor_user_id)."""
    # Pool reducido de identidades para forzar colisiones propietario/actor.
    owners = draw(st.lists(_user_ids, min_size=2, max_size=4, unique=True))
    n_entries = draw(st.integers(min_value=1, max_value=6))
    entries = []
    for i in range(n_entries):
        entries.append({
            "provider": draw(_letters),
            "category": draw(st.sampled_from(ROLE_KEYS)),
            # nombre/enlace únicos por índice para evitar deduplicación del store.
            "name": f"{draw(_letters)}{i}",
            "link": f"https://{draw(_letters)}{i}.example",
            "owner": str(draw(st.sampled_from(owners))),
        })
    # Secuencia de borrados: cada uno elige una entrada y un actor del pool.
    ops = draw(st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=n_entries - 1),
            st.sampled_from(owners),
        ),
        min_size=1, max_size=12))
    ops = [(idx, str(actor)) for idx, actor in ops]
    return entries, ops


@settings(max_examples=10, deadline=None,
          suppress_health_check=[HealthCheck.too_slow,
                                 HealthCheck.function_scoped_fixture])
@given(_ownership_scenario())
def test_only_owner_can_delete_entry(scenario):
    """A lo largo de una secuencia de borrados, una entrada solo desaparece por
    acción de su propietario; cualquier otro intento devuelve 403 y la conserva.

    **Validates: Requirements 8.5, 12.3** (Property 5)
    """
    entries, ops = scenario

    tmpdir = tempfile.mkdtemp(prefix="mhub_write_pbt5_")
    try:
        manager, registry_store = _make_stores(tmpdir)
        app = build_app(_make_bot(manager, registry_store))

        # Poblamos el store y casamos cada entrada con su id de fila real.
        for entry in entries:
            registry_store.add(
                entry["provider"], entry["category"], entry["name"],
                entry["link"], int(entry["owner"]))

        rows = registry_store.all_entries()
        # Mapa índice-de-generación → (entry_id, owner). Se asume orden de inserción.
        assert len(rows) == len(entries)
        index_to_row = []
        for row, entry in zip(rows, entries):
            assert str(row["user_id"]) == entry["owner"]
            index_to_row.append({"id": row["id"], "owner": entry["owner"]})

        present = {r["id"] for r in index_to_row}

        for idx, actor in ops:
            target = index_to_row[idx]
            entry_id = target["id"]
            owner = target["owner"]

            before = {e["id"] for e in registry_store.all_entries()}
            client = _client_as(app, actor)
            resp = client.request("DELETE", f"/api/registry/{entry_id}")
            after = {e["id"] for e in registry_store.all_entries()}

            if entry_id not in before:
                # Ya estaba borrada: idempotente → 404, sin cambios.
                assert resp.status_code == 404, resp.text
                assert after == before
            elif actor == owner:
                # Propietario: se borra (204) y desaparece.
                assert resp.status_code == 204, resp.text
                assert entry_id not in after
                present.discard(entry_id)
            else:
                # No propietario: 403 y la entrada se conserva intacta.
                assert resp.status_code == 403, resp.text
                assert entry_id in after
                assert after == before

        # Invariante global: toda entrada que sobrevive conserva su propietario;
        # ninguna fue borrada por una identidad ajena.
        survivors = {r["id"]: str(r["user_id"]) for r in registry_store.all_entries()}
        for target in index_to_row:
            if target["id"] in survivors:
                assert survivors[target["id"]] == target["owner"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

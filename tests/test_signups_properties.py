"""Tests de propiedad (property-based) de la capa de dominio de eventos.

Usan Hypothesis para generar secuencias aleatorias de operaciones de inscripción
(join / leave / move vía ``toggle``) sobre múltiples usuarios y roles, y verifican
que los invariantes del dominio se mantienen tras CADA operación.

Propiedades cubiertas (ver design.md → "Correctness Properties"):

- Property 2 — Cupos no excedidos: para todo evento y rol,
  ``len(inscritos[rol]) <= plazas[rol]`` tras cualquier secuencia de operaciones.
  **Validates: Requirements 8.1, 8.6**

- Property 3 — Una persona, un rol por evento: una misma Identidad nunca aparece
  en más de un rol del mismo evento (``toggle`` mueve, no duplica).
  **Validates: Requirements 8.1**

- Property 9 — Validación sin efectos secundarios: validar una composición no la
  modifica; una composición válida sigue siendo válida tras normalizarse.
  **Validates: Requirements 2.3**
"""
import json
import os
import shutil
import tempfile

from hypothesis import given, settings, strategies as st
from hypothesis import HealthCheck

from tests.domain_loader import EventManager, EventStore


# Conjunto acotado de claves de rol y de usuarios para que las secuencias
# aleatorias provoquen colisiones reales (llenado de roles, movimientos entre
# roles, re-toggles del mismo usuario), no solo inserciones dispersas.
ROLE_KEYS = ["backend", "frontend", "qa", "devops", "design"]
USER_IDS = [1, 2, 3, 4, 5, 6]


# --------------------------------------------------------------------------- #
# Estrategias
# --------------------------------------------------------------------------- #
@st.composite
def compositions(draw):
    """Genera una Composicion válida: 1..N roles únicos con plazas enteras ≥1.

    Plazas pequeñas (1..3) para que los roles se llenen con frecuencia dentro de
    las secuencias generadas, ejercitando el camino FULL y la unicidad de rol.
    """
    keys = draw(st.lists(
        st.sampled_from(ROLE_KEYS),
        min_size=1, max_size=len(ROLE_KEYS), unique=True))
    composition = {}
    for key in keys:
        plazas = draw(st.integers(min_value=1, max_value=3))
        emoji = draw(st.one_of(st.none(), st.sampled_from(["A", "B", "C"])))
        composition[key] = {"emoji": emoji, "plazas": plazas}
    return composition


@st.composite
def scenarios(draw):
    """Genera (composición, operaciones).

    Cada operación es un par ``(user_id, role_key)`` con role_key restringido a
    los roles de la composición generada, de modo que las operaciones siempre
    apuntan a roles existentes del evento.
    """
    composition = draw(compositions())
    role_keys = list(composition.keys())
    operations = draw(st.lists(
        st.tuples(st.sampled_from(USER_IDS), st.sampled_from(role_keys)),
        min_size=0, max_size=40))
    return composition, operations


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _fresh_manager():
    """Crea un EventManager con un EventStore SQLite en un directorio temporal.

    Se evita el fixture ``tmp_path`` de pytest porque Hypothesis reejecuta el
    cuerpo del test muchas veces; cada ejemplo necesita una base de datos limpia.
    """
    tmpdir = tempfile.mkdtemp(prefix="mhub_pbt_")
    db_path = os.path.join(tmpdir, "events.sqlite")
    store = EventStore(db_path, templates={})
    return EventManager(store, {}), tmpdir


def _assert_invariants(event, composition):
    """Comprueba Property 2 y Property 3 sobre el estado de un evento."""
    state = event["state"]

    # Property 2: ningún rol supera su número de plazas.
    for role_key, role_cfg in composition.items():
        occupied = len(state.get(role_key, []))
        assert occupied <= role_cfg["plazas"], (
            f"Rol '{role_key}' excede plazas: {occupied} > {role_cfg['plazas']}")

    # Property 3: una misma Identidad nunca aparece en más de un rol.
    seen_roles = {}
    for role_key, members in state.items():
        for member in members:
            uid = member["id"]
            assert uid not in seen_roles, (
                f"Usuario {uid} aparece en '{role_key}' y "
                f"'{seen_roles[uid]}' del mismo evento")
            seen_roles[uid] = role_key


# --------------------------------------------------------------------------- #
# Property 2 y 3: cupos y unicidad de rol tras secuencias aleatorias
# --------------------------------------------------------------------------- #
@settings(max_examples=10, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(scenarios())
def test_capacity_and_unique_role_invariants(scenario):
    """Property 2 y Property 3 se mantienen tras CADA operación de una secuencia
    aleatoria de join/leave/move sobre múltiples usuarios y roles.

    **Validates: Requirements 8.1, 8.6**
    """
    composition, operations = scenario
    manager, tmpdir = _fresh_manager()
    try:
        message_id = 1
        manager.create(message_id, 10, 100, "PBT", composition)

        # Invariante también en el estado inicial (todos los roles vacíos).
        _assert_invariants(manager.get(message_id), composition)

        for user_id, role_key in operations:
            manager.toggle(message_id, role_key, user_id, f"user{user_id}")
            # Se relee del store para validar el estado realmente persistido.
            persisted = manager.get(message_id)
            _assert_invariants(persisted, composition)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Property 9: validación sin efectos secundarios
# --------------------------------------------------------------------------- #
@settings(max_examples=10, deadline=None)
@given(compositions())
def test_validation_has_no_side_effects(composition):
    """Validar una composición no la modifica y una composición válida sigue
    siendo válida tras normalizarse (idempotencia de la normalización).

    **Validates: Requirements 2.3**
    """
    manager = EventManager(store=None, templates={})

    snapshot = json.loads(json.dumps(composition))
    normalized = manager.validate_composition(composition)

    # No hay efectos secundarios: la entrada original no se muta.
    assert composition == snapshot

    # Una composición válida sigue siendo válida tras normalizarse, y la
    # normalización es idempotente.
    revalidated = manager.validate_composition(normalized)
    assert revalidated == normalized

"""Tests unitarios del Modulo_Puente (BridgeService).

Cubren el matching por clave normalizada entre roles de un Evento y las
Personas del directorio (Req 4.2), la exclusión de las Personas ya inscritas en
el Evento (Req 4.5), los roles sin Personas correspondientes (Req 4.4) y, de
forma complementaria, `events_for_skill` (guild-scoped) y el caso de evento
inexistente.

BridgeService y RegistryStore son agnósticos de discord pero viven en paquetes
cuyo `__init__.py` importa discord; por eso se cargan por ruta vía
`tests.domain_loader`. Se construyen stores reales sobre sqlite temporal y un
EventManager real: el puente solo lee de ambos stores (Req 4.6).

Requisitos: 4.2, 4.4, 4.5
"""
import pytest

from tests.domain_loader import (
    BridgeService,
    EventManager,
    EventStore,
    Persona,
    RegistryStore,
)


@pytest.fixture
def registry(tmp_path):
    store = RegistryStore(str(tmp_path / "registry.sqlite"))
    # Dos candidatos de "backend" (uno por norm_category), uno de "frontend" y
    # uno cuyo NOMBRE acentuado normaliza a "programacion".
    store.add("Alice", "Backend", "Python", "https://a", 1)
    store.add("Dave", "Backend", "Go", "https://d", 4)
    store.add("Bob", "Frontend", "React", "https://b", 2)
    store.add("Carol", "Lenguajes", "Programación", "https://c", 3)
    return store


@pytest.fixture
def events(tmp_path):
    return EventStore(str(tmp_path / "events.sqlite"))


@pytest.fixture
def manager(events):
    return EventManager(events, templates={})


@pytest.fixture
def composition():
    # qa no tiene ningún candidato en el directorio (Req 4.4).
    return {
        "backend": {"emoji": None, "plazas": 2},
        "frontend": {"emoji": None, "plazas": 1},
        "programacion": {"emoji": None, "plazas": 1},
        "qa": {"emoji": None, "plazas": 1},
    }


@pytest.fixture
def event_id(manager, composition):
    mid = 1000
    manager.create(mid, channel_id=10, guild_id=42, title="Equipo tarea",
                   composition=composition)
    return mid


@pytest.fixture
def bridge(registry, events):
    return BridgeService(registry, events)


# --------------------------------------------------------------------------- #
# Matching por clave normalizada (Req 4.2)
# --------------------------------------------------------------------------- #
class TestMatching:

    def test_role_matches_by_normalized_category(self, bridge, event_id):
        """El rol "backend" casa con entradas cuya categoría es "Backend".

        **Validates: Requirements 4.2**
        """
        suggestions = bridge.suggest_for_event(event_id)
        backend = suggestions["backend"]
        assert all(isinstance(p, Persona) for p in backend)
        assert {p.user_id for p in backend} == {1, 4}

    def test_role_matches_accented_name_variant(self, bridge, event_id):
        """El rol "programacion" casa con un nombre acentuado "Programación".

        **Validates: Requirements 4.2**
        """
        suggestions = bridge.suggest_for_event(event_id)
        programacion = suggestions["programacion"]
        assert [p.user_id for p in programacion] == [3]
        assert programacion[0].name == "Programación"

    def test_returns_persona_with_full_directory_data(self, bridge, event_id):
        """Cada candidato es una Persona con los datos de su entrada.

        **Validates: Requirements 4.2**
        """
        suggestions = bridge.suggest_for_event(event_id)
        alice = next(p for p in suggestions["backend"] if p.user_id == 1)
        assert alice == Persona(
            user_id=1, provider="Alice", category="Backend",
            name="Python", link="https://a")


# --------------------------------------------------------------------------- #
# Exclusión de inscritos (Req 4.5)
# --------------------------------------------------------------------------- #
class TestExclusion:

    def test_excludes_user_enrolled_in_same_role(self, bridge, manager, event_id):
        """Una Persona inscrita en el rol queda excluida de sus sugerencias.

        **Validates: Requirements 4.5**
        """
        manager.toggle(event_id, "backend", user_id=1, user_name="Alice")
        backend = bridge.suggest_for_event(event_id)["backend"]
        assert {p.user_id for p in backend} == {4}

    def test_excludes_user_enrolled_in_any_other_role(self, bridge, manager, event_id):
        """Una Persona inscrita en CUALQUIER rol del evento queda excluida de
        las sugerencias de todos los roles.

        **Validates: Requirements 4.5**
        """
        # Apuntamos a la persona 1 (candidata de backend) en el rol frontend.
        manager.toggle(event_id, "frontend", user_id=1, user_name="Alice")
        suggestions = bridge.suggest_for_event(event_id)
        assert {p.user_id for p in suggestions["backend"]} == {4}


# --------------------------------------------------------------------------- #
# Rol sin candidatos (Req 4.4)
# --------------------------------------------------------------------------- #
class TestNoCandidates:

    def test_role_without_directory_match_returns_empty_list(self, bridge, event_id):
        """Un rol sin Personas correspondientes aparece con lista vacía.

        **Validates: Requirements 4.4**
        """
        suggestions = bridge.suggest_for_event(event_id)
        assert "qa" in suggestions
        assert suggestions["qa"] == []

    def test_every_role_present_in_result(self, bridge, event_id, composition):
        """El resultado incluye una clave por cada rol de la composición.

        **Validates: Requirements 4.4**
        """
        suggestions = bridge.suggest_for_event(event_id)
        assert set(suggestions.keys()) == set(composition.keys())


# --------------------------------------------------------------------------- #
# Casos complementarios: events_for_skill y evento inexistente
# --------------------------------------------------------------------------- #
class TestComplementary:

    def test_missing_event_returns_empty_dict(self, bridge):
        assert bridge.suggest_for_event(999999) == {}

    def test_events_for_skill_is_guild_scoped_and_matches_role(
            self, bridge, manager, event_id):
        # Evento en otro guild con un rol "backend": no debe aparecer al
        # consultar el guild 42.
        manager.create(2000, channel_id=20, guild_id=99, title="Otro guild",
                       composition={"backend": {"emoji": None, "plazas": 1}})
        matched = bridge.events_for_skill(42, "Backend")
        assert [e["message_id"] for e in matched] == [event_id]

    def test_events_for_skill_returns_empty_when_no_role_matches(
            self, bridge, event_id):
        assert bridge.events_for_skill(42, "devops") == []

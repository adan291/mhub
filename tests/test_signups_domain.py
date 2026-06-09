"""Tests unitarios de la capa de dominio de eventos (signups).

Cubren composición ad-hoc (válida/ inválida), inscripción (toggle: join/leave/
move/full), ciclo de vida (transición a 'completado', archive, listados) y la
migración perezosa de eventos antiguos sin `composition_json`.

Requisitos: 2.1, 2.3, 2.4, 2.5, 2.6, 11.2, 11.3, 11.4, 15.4
"""
import json
import sqlite3

import pytest

from tests.domain_loader import EventManager, EventStore, CompositionError


# Plantillas de prueba con la misma forma que config/templates.json:
# { template_key: {"titulo": str, "roles": {key: {"emoji": str, "plazas": int}}}}
TEMPLATES = {
    "mitica": {
        "titulo": "Grupo para míticas",
        "roles": {
            "tank": {"emoji": "T", "plazas": 1},
            "dps": {"emoji": "D", "plazas": 3},
            "heal": {"emoji": "H", "plazas": 1},
        },
    },
    "dnd": {
        "titulo": "Partida de rol",
        "roles": {
            "dm": {"emoji": "M", "plazas": 1},
            "jugador": {"emoji": "J", "plazas": 2},
        },
    },
}


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "events.sqlite")
    return EventStore(db_path, templates=TEMPLATES)


@pytest.fixture
def manager(store):
    return EventManager(store, TEMPLATES)


def _adhoc():
    """Composición ad-hoc válida sencilla: backend:2, frontend:1."""
    return {
        "backend": {"emoji": None, "plazas": 2},
        "frontend": {"emoji": None, "plazas": 1},
    }


# --------------------------------------------------------------------------- #
# Validación de composición (Req 2.3, 2.4, 2.5)
# --------------------------------------------------------------------------- #
class TestValidateComposition:
    def test_valid_dict_is_normalized(self, manager):
        result = manager.validate_composition(
            {"backend": {"emoji": "B", "plazas": 2}})
        assert result == {"backend": {"emoji": "B", "plazas": 2}}

    def test_valid_pairs_sequence(self, manager):
        result = manager.validate_composition(
            [("backend", {"plazas": 2}), ("qa", {"plazas": 1})])
        assert result == {
            "backend": {"emoji": None, "plazas": 2},
            "qa": {"emoji": None, "plazas": 1},
        }

    def test_empty_composition_rejected(self, manager):
        with pytest.raises(CompositionError) as exc:
            manager.validate_composition({})
        assert exc.value.code == "empty"

    @pytest.mark.parametrize("bad_plazas", [0, -1, 1.5, "2", None])
    def test_invalid_plazas_rejected(self, manager, bad_plazas):
        with pytest.raises(CompositionError) as exc:
            manager.validate_composition({"backend": {"plazas": bad_plazas}})
        assert exc.value.code == "invalid_plazas"
        assert exc.value.role == "backend"

    def test_bool_is_not_valid_plazas(self, manager):
        # bool es subclase de int, debe excluirse explícitamente.
        with pytest.raises(CompositionError) as exc:
            manager.validate_composition({"backend": {"plazas": True}})
        assert exc.value.code == "invalid_plazas"

    def test_duplicate_key_rejected(self, manager):
        with pytest.raises(CompositionError) as exc:
            manager.validate_composition(
                [("backend", {"plazas": 2}), ("backend", {"plazas": 1})])
        assert exc.value.code == "duplicate_key"
        assert exc.value.role == "backend"

    def test_validation_has_no_side_effects(self, manager):
        # Property 9: validar no modifica la entrada original.
        original = {"backend": {"emoji": "B", "plazas": 2}}
        snapshot = json.loads(json.dumps(original))
        manager.validate_composition(original)
        assert original == snapshot


# --------------------------------------------------------------------------- #
# Creación de eventos (Req 2.1, 2.2, 2.6)
# --------------------------------------------------------------------------- #
class TestCreate:
    def test_create_adhoc_persists_composition(self, manager, store):
        manager.create(1, 10, 100, "Sprint", _adhoc(), template_key=None)
        event = store.get(1)
        assert event["composition"] == _adhoc()
        assert event["status"] == EventManager.ACTIVE
        # El estado de ocupación arranca vacío para cada rol.
        assert event["state"] == {"backend": [], "frontend": []}

    def test_create_from_template_copies_composition(self, manager, store):
        # Req 2.6: crear desde plantilla copia su composición al evento.
        composition = manager.composition_from_template("mitica")
        manager.create(2, 10, 100, "Mítica", composition, template_key="mitica")
        event = store.get(2)
        assert event["composition"] == TEMPLATES["mitica"]["roles"]

    def test_create_rejects_invalid_composition(self, manager, store):
        with pytest.raises(CompositionError):
            manager.create(3, 10, 100, "Bad", {"backend": {"plazas": 0}})
        assert store.get(3) is None


# --------------------------------------------------------------------------- #
# Inscripción: toggle join / leave / move / full (Req 2.1)
# --------------------------------------------------------------------------- #
class TestToggle:
    def test_join(self, manager):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        result, event = manager.toggle(1, "backend", 7, "Ana")
        assert result == EventManager.JOINED
        assert event["state"]["backend"] == [{"id": 7, "name": "Ana"}]

    def test_leave_when_toggling_same_role(self, manager):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        manager.toggle(1, "backend", 7, "Ana")
        result, event = manager.toggle(1, "backend", 7, "Ana")
        assert result == EventManager.LEFT
        assert event["state"]["backend"] == []

    def test_move_between_roles(self, manager):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        manager.toggle(1, "backend", 7, "Ana")
        result, event = manager.toggle(1, "frontend", 7, "Ana")
        assert result == EventManager.MOVED
        # Property 3: una persona en un solo rol.
        assert event["state"]["backend"] == []
        assert event["state"]["frontend"] == [{"id": 7, "name": "Ana"}]

    def test_full_role_rejects_new_member(self, manager):
        manager.create(1, 10, 100, "Sprint", _adhoc())  # frontend tiene 1 plaza
        manager.toggle(1, "frontend", 7, "Ana")
        result, _ = manager.toggle(1, "frontend", 8, "Beto")
        assert result == EventManager.FULL

    def test_unknown_role_returns_not_found(self, manager):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        result, event = manager.toggle(1, "noexiste", 7, "Ana")
        assert result == EventManager.NOT_FOUND
        assert event is None

    def test_unknown_event_returns_not_found(self, manager):
        result, event = manager.toggle(999, "backend", 7, "Ana")
        assert result == EventManager.NOT_FOUND
        assert event is None


# --------------------------------------------------------------------------- #
# Ciclo de vida: completado / activo / archivado / listados (Req 11.2-11.4)
# --------------------------------------------------------------------------- #
class TestLifecycle:
    def test_transition_to_completed_when_full(self, manager, store):
        # Req 11.2: al llenarse todos los roles pasa a 'completado'.
        comp = {"solo": {"emoji": None, "plazas": 1}}
        manager.create(1, 10, 100, "Uno", comp)
        _, event = manager.toggle(1, "solo", 7, "Ana")
        assert event["status"] == EventManager.COMPLETED
        assert store.get(1)["status"] == EventManager.COMPLETED

    def test_leaving_completed_event_returns_to_active(self, manager, store):
        comp = {"solo": {"emoji": None, "plazas": 1}}
        manager.create(1, 10, 100, "Uno", comp)
        manager.toggle(1, "solo", 7, "Ana")
        _, event = manager.toggle(1, "solo", 7, "Ana")
        assert event["status"] == EventManager.ACTIVE
        assert store.get(1)["status"] == EventManager.ACTIVE

    def test_partial_fill_stays_active(self, manager):
        manager.create(1, 10, 100, "Sprint", _adhoc())  # backend:2, frontend:1
        _, event = manager.toggle(1, "backend", 7, "Ana")
        assert event["status"] == EventManager.ACTIVE

    def test_archive(self, manager, store):
        # Req 11.3.
        manager.create(1, 10, 100, "Sprint", _adhoc())
        event = manager.archive(1)
        assert event["status"] == EventManager.ARCHIVED
        assert store.get(1)["status"] == EventManager.ARCHIVED

    def test_archive_unknown_event_returns_none(self, manager):
        assert manager.archive(404) is None

    def test_archived_event_not_reactivated_by_toggle(self, manager, store):
        # Req 11.3: archivar es explícito; toggle no debe sacarlo de 'archivado'.
        comp = {"solo": {"emoji": None, "plazas": 1}}
        manager.create(1, 10, 100, "Uno", comp)
        manager.archive(1)
        _, event = manager.toggle(1, "solo", 7, "Ana")
        assert event["status"] == EventManager.ARCHIVED

    def test_list_active_and_archived(self, manager):
        # Req 11.4: historial archivado separado de los activos.
        manager.create(1, 10, 100, "Activo", _adhoc())
        manager.create(2, 10, 100, "Archivado", _adhoc())
        manager.archive(2)

        active_ids = [e["message_id"] for e in manager.list_active()]
        archived_ids = [e["message_id"] for e in manager.list_archived()]
        assert active_ids == [1]
        assert archived_ids == [2]

    def test_list_filters_by_guild(self, manager):
        manager.create(1, 10, 100, "Guild A", _adhoc())
        manager.create(2, 10, 200, "Guild B", _adhoc())
        active_a = [e["message_id"] for e in manager.list_active(guild_id=100)]
        assert active_a == [1]


# --------------------------------------------------------------------------- #
# Migración perezosa de eventos antiguos (Req 15.4)
# --------------------------------------------------------------------------- #
class TestLazyMigration:
    @staticmethod
    def _create_legacy_db(db_path):
        """Crea un events.sqlite con el esquema antiguo (sin composition_json
        ni status) y una fila previa a esta feature."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE events (
                message_id   INTEGER PRIMARY KEY,
                channel_id   INTEGER NOT NULL,
                guild_id     INTEGER,
                template_key TEXT,
                title        TEXT NOT NULL,
                state_json   TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now'))
            )
            """
        )
        state = {"tank": [{"id": 7, "name": "Ana"}], "dps": [], "heal": []}
        conn.execute(
            "INSERT INTO events (message_id, channel_id, guild_id, "
            "template_key, title, state_json) VALUES (?, ?, ?, ?, ?, ?)",
            (111, 10, 100, "mitica", "Evento antiguo", json.dumps(state)),
        )
        conn.commit()
        conn.close()

    def test_composition_reconstructed_from_template(self, tmp_path):
        db_path = str(tmp_path / "events.sqlite")
        self._create_legacy_db(db_path)

        store = EventStore(db_path, templates=TEMPLATES)
        event = store.get(111)

        # La composición se reconstruye desde templates.json[template_key].
        assert event["composition"] == TEMPLATES["mitica"]["roles"]
        # status ausente en filas antiguas → por defecto 'activo'.
        assert event["status"] == EventManager.ACTIVE
        # El estado de ocupación previo se conserva intacto.
        assert event["state"]["tank"] == [{"id": 7, "name": "Ana"}]

    def test_backfill_persists_composition(self, tmp_path):
        db_path = str(tmp_path / "events.sqlite")
        self._create_legacy_db(db_path)

        store = EventStore(db_path, templates=TEMPLATES)
        store.get(111)  # dispara la migración perezosa y el backfill

        # Tras cargar una vez, composition_json queda persistido en la fila.
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT composition_json FROM events WHERE message_id = 111"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert json.loads(row[0]) == TEMPLATES["mitica"]["roles"]

    def test_event_manager_handles_legacy_event(self, tmp_path):
        # Req 15.4: un evento de plantilla previo se gestiona sin migración manual.
        db_path = str(tmp_path / "events.sqlite")
        self._create_legacy_db(db_path)
        store = EventStore(db_path, templates=TEMPLATES)
        manager = EventManager(store, TEMPLATES)

        result, event = manager.toggle(111, "dps", 8, "Beto")
        assert result == EventManager.JOINED
        assert {"id": 8, "name": "Beto"} in event["state"]["dps"]

    def test_unknown_template_reconstructs_empty(self, tmp_path):
        # Si la plantilla ya no existe, la composición reconstruida es {}.
        db_path = str(tmp_path / "events.sqlite")
        self._create_legacy_db(db_path)
        store = EventStore(db_path, templates={})  # sin 'mitica'
        event = store.get(111)
        assert event["composition"] == {}

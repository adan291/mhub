"""Tests de GuildConfigStore: persistencia de la Configuracion_Por_Servidor
(plantillas y categorías por `guild_id`).

Cubren:
  - Resolución con fallback a la configuración por defecto cuando el Servidor no
    tiene config propia (Req 3.3).
  - Resolución usando la config propia del Servidor cuando existe.
  - La primera modificación de un Servidor crea sus filas sin paso de
    configuración previo (Req 3.4).
  - Aislamiento entre Servidores: un cambio en un `guild_id` no altera la
    configuración de otro (Req 3.5).
  - Property 8 (property-based, Hypothesis): secuencias aleatorias de
    `set_template`/`add_category` sobre múltiples `guild_id` nunca filtran
    configuración entre servidores.
"""
import os
import shutil
import tempfile

import pytest
from hypothesis import given, settings, strategies as st
from hypothesis import HealthCheck

from tests.domain_loader import GuildConfigStore


# Configuración por defecto de la Plataforma usada como fallback (Req 3.3).
DEFAULT_TEMPLATES = {
    "raid": {"titulo": "Raid", "roles": {"tank": {"emoji": "T", "plazas": 2}}},
    "mitica": {"titulo": "Mítica", "roles": {"dps": {"emoji": "D", "plazas": 3}}},
}
DEFAULT_CATEGORIES = ["arte", "musica", "programacion"]


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "guild_config.sqlite")
    return GuildConfigStore(
        db_path,
        default_templates=DEFAULT_TEMPLATES,
        default_categories=DEFAULT_CATEGORIES,
    )


# --------------------------------------------------------------------------- #
# Resolución con fallback a la config por defecto (Req 3.3)
# --------------------------------------------------------------------------- #
class TestResolutionFallback:
    def test_resolve_templates_falls_back_to_defaults_without_config(self, store):
        """Un Servidor sin config propia recibe las plantillas por defecto."""
        assert store.resolve_templates(guild_id=111) == DEFAULT_TEMPLATES

    def test_resolve_categories_falls_back_to_defaults_without_config(self, store):
        assert store.resolve_categories(guild_id=111) == DEFAULT_CATEGORIES

    def test_resolve_template_falls_back_to_default_by_key(self, store):
        assert store.resolve_template(222, "raid") == DEFAULT_TEMPLATES["raid"]

    def test_resolve_unknown_template_returns_none(self, store):
        assert store.resolve_template(222, "inexistente") is None

    def test_resolve_templates_returns_copy_not_shared_state(self, store):
        """La resolución por defecto devuelve una copia: mutarla no contamina
        la config por defecto compartida."""
        resolved = store.resolve_templates(333)
        resolved["raid"]["titulo"] = "MUTADO"
        # La siguiente resolución sigue viendo el valor por defecto original.
        assert store.resolve_templates(333)["raid"]["titulo"] == "Raid"


# --------------------------------------------------------------------------- #
# Resolución usando la config propia del Servidor (Req 3.2)
# --------------------------------------------------------------------------- #
class TestResolutionOwnConfig:
    def test_resolve_templates_uses_own_when_present(self, store):
        roles = {"backend": {"emoji": "B", "plazas": 2}}
        store.set_template(111, "sprint", "Sprint", roles)

        resolved = store.resolve_templates(111)
        assert resolved == {"sprint": {"titulo": "Sprint", "roles": roles}}
        # No se mezcla con los defaults una vez que el Servidor tiene config.
        assert "raid" not in resolved

    def test_resolve_categories_uses_own_when_present(self, store):
        store.add_category(111, "qa")
        assert store.resolve_categories(111) == ["qa"]

    def test_resolve_template_by_key_uses_own(self, store):
        roles = {"backend": {"emoji": None, "plazas": 1}}
        store.set_template(111, "sprint", "Sprint", roles)
        assert store.resolve_template(111, "sprint") == {
            "titulo": "Sprint", "roles": roles}


# --------------------------------------------------------------------------- #
# La primera modificación crea las filas del Servidor (Req 3.4)
# --------------------------------------------------------------------------- #
class TestFirstModificationCreatesRows:
    def test_set_template_creates_rows_on_first_modification(self, store):
        assert store.get_guild_templates(111) == {}  # sin filas previas
        roles = {"backend": {"emoji": "B", "plazas": 2}}
        store.set_template(111, "sprint", "Sprint", roles)
        assert store.get_guild_templates(111) == {
            "sprint": {"titulo": "Sprint", "roles": roles}}

    def test_add_category_creates_rows_on_first_modification(self, store):
        assert store.get_guild_categories(111) == []
        assert store.add_category(111, "qa") is True
        assert store.get_guild_categories(111) == ["qa"]

    def test_add_duplicate_category_is_noop(self, store):
        assert store.add_category(111, "qa") is True
        assert store.add_category(111, "qa") is False
        assert store.get_guild_categories(111) == ["qa"]

    def test_set_template_updates_existing(self, store):
        store.set_template(111, "sprint", "Sprint", {"a": {"emoji": None, "plazas": 1}})
        store.set_template(111, "sprint", "Sprint v2", {"b": {"emoji": None, "plazas": 2}})
        assert store.get_guild_templates(111) == {
            "sprint": {"titulo": "Sprint v2", "roles": {"b": {"emoji": None, "plazas": 2}}}}

    def test_delete_template_and_remove_category(self, store):
        store.set_template(111, "sprint", "Sprint", {"a": {"emoji": None, "plazas": 1}})
        store.add_category(111, "qa")
        assert store.delete_template(111, "sprint") is True
        assert store.delete_template(111, "sprint") is False
        assert store.remove_category(111, "qa") is True
        assert store.remove_category(111, "qa") is False


# --------------------------------------------------------------------------- #
# Aislamiento entre Servidores (Req 3.5)
# --------------------------------------------------------------------------- #
class TestGuildIsolation:
    def test_template_change_in_one_guild_does_not_affect_another(self, store):
        store.set_template(111, "sprint", "Sprint", {"a": {"emoji": None, "plazas": 1}})

        # El Servidor 222 no tiene config propia: sigue viendo los defaults.
        assert store.get_guild_templates(222) == {}
        assert store.resolve_templates(222) == DEFAULT_TEMPLATES

    def test_category_change_in_one_guild_does_not_affect_another(self, store):
        store.add_category(111, "qa")
        assert store.get_guild_categories(222) == []
        assert store.resolve_categories(222) == DEFAULT_CATEGORIES

    def test_two_guilds_keep_independent_config(self, store):
        store.set_template(111, "sprint", "Sprint A", {"a": {"emoji": None, "plazas": 1}})
        store.set_template(222, "sprint", "Sprint B", {"b": {"emoji": None, "plazas": 2}})
        store.add_category(111, "qa")
        store.add_category(222, "arte")

        assert store.get_guild_templates(111)["sprint"]["titulo"] == "Sprint A"
        assert store.get_guild_templates(222)["sprint"]["titulo"] == "Sprint B"
        assert store.get_guild_categories(111) == ["qa"]
        assert store.get_guild_categories(222) == ["arte"]

    def test_delete_in_one_guild_does_not_touch_another(self, store):
        store.set_template(111, "sprint", "Sprint", {"a": {"emoji": None, "plazas": 1}})
        store.set_template(222, "sprint", "Sprint", {"a": {"emoji": None, "plazas": 1}})
        store.delete_template(111, "sprint")
        # El Servidor 222 conserva la suya intacta.
        assert store.get_guild_templates(222) == {
            "sprint": {"titulo": "Sprint", "roles": {"a": {"emoji": None, "plazas": 1}}}}


# --------------------------------------------------------------------------- #
# Property 8 (property-based): aislamiento por `guild_id`
#
# "Una operación con un `guild_id` nunca lee ni modifica la
# Configuracion_Por_Servidor de otro `guild_id`."
# **Validates: Requirements 3.5**
#
# Estrategia: se generan secuencias aleatorias de operaciones de escritura
# (set_template / add_category / delete_template / remove_category) sobre un
# conjunto acotado de `guild_id`. En paralelo se mantiene un MODELO de
# referencia (dict por guild). Tras CADA operación se verifica que la vista
# persistida de TODOS los guilds coincide con el modelo. Si una operación
# filtrara configuración a otro guild, la vista de ese otro guild divergiría del
# modelo (que solo actualiza el guild objetivo) y el test fallaría.
# --------------------------------------------------------------------------- #
GUILD_IDS = [111, 222, 333]
TEMPLATE_KEYS = ["sprint", "raid", "mitica"]
CATEGORY_NAMES = ["qa", "arte", "backend", "frontend"]


def _roles_strategy():
    return st.dictionaries(
        keys=st.sampled_from(["a", "b", "c"]),
        values=st.fixed_dictionaries({
            "emoji": st.one_of(st.none(), st.sampled_from(["X", "Y"])),
            "plazas": st.integers(min_value=1, max_value=4),
        }),
        min_size=1, max_size=3,
    )


@st.composite
def operations(draw):
    """Genera una operación de escritura dirigida a un `guild_id` concreto."""
    op = draw(st.sampled_from(
        ["set_template", "delete_template", "add_category", "remove_category"]))
    guild_id = draw(st.sampled_from(GUILD_IDS))
    if op == "set_template":
        return (op, guild_id, draw(st.sampled_from(TEMPLATE_KEYS)),
                draw(st.text(min_size=0, max_size=8)), draw(_roles_strategy()))
    if op == "delete_template":
        return (op, guild_id, draw(st.sampled_from(TEMPLATE_KEYS)))
    if op == "add_category":
        return (op, guild_id, draw(st.sampled_from(CATEGORY_NAMES)))
    return (op, guild_id, draw(st.sampled_from(CATEGORY_NAMES)))


@settings(max_examples=10, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(operations(), min_size=0, max_size=40))
def test_property_8_per_guild_isolation(ops):
    """Property 8: secuencias aleatorias de operaciones sobre múltiples
    `guild_id` nunca filtran configuración entre servidores.

    **Validates: Requirements 3.5**
    """
    tmpdir = tempfile.mkdtemp(prefix="mhub_gcs_pbt_")
    try:
        store = GuildConfigStore(
            os.path.join(tmpdir, "guild_config.sqlite"),
            default_templates=DEFAULT_TEMPLATES,
            default_categories=DEFAULT_CATEGORIES,
        )

        # Modelo de referencia: solo el guild objetivo de cada op se actualiza.
        model_templates = {g: {} for g in GUILD_IDS}
        model_categories = {g: set() for g in GUILD_IDS}

        def assert_all_guilds_match():
            for g in GUILD_IDS:
                assert store.get_guild_templates(g) == model_templates[g], (
                    f"Filtración de plantillas detectada en guild {g}")
                assert sorted(store.get_guild_categories(g)) == sorted(
                    model_categories[g]), (
                    f"Filtración de categorías detectada en guild {g}")

        assert_all_guilds_match()  # estado inicial: todos vacíos

        for op in ops:
            kind, guild_id = op[0], op[1]
            if kind == "set_template":
                _, _, key, title, roles = op
                store.set_template(guild_id, key, title, roles)
                model_templates[guild_id][key] = {"titulo": title, "roles": roles}
            elif kind == "delete_template":
                _, _, key = op
                store.delete_template(guild_id, key)
                model_templates[guild_id].pop(key, None)
            elif kind == "add_category":
                _, _, cat = op
                store.add_category(guild_id, cat)
                model_categories[guild_id].add(cat)
            elif kind == "remove_category":
                _, _, cat = op
                store.remove_category(guild_id, cat)
                model_categories[guild_id].discard(cat)

            # Tras CADA operación, TODOS los guilds deben coincidir con el modelo.
            assert_all_guilds_match()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

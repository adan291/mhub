"""Tests unitarios del parser de composición ad-hoc (Req 2.1, 2.3, 2.4, 2.5).

El parser convierte una cadena 'rol:plazas, ...' en una lista de pares apta
para EventManager.validate_composition. Probamos entradas válidas (uno/varios
roles, espacios, sin emoji) e inválidas (vacía, sin ':', plazas no enteras,
cero/negativas y claves duplicadas).
"""
import pytest

from tests.domain_loader import (
    EventManager,
    EventStore,
    ParserCompositionError,
    parse_composition,
)


# --------------------------------------------------------------------------- #
# Entradas válidas
# --------------------------------------------------------------------------- #
def test_parses_single_role():
    result = parse_composition("backend:2")
    assert result == [("backend", {"emoji": None, "plazas": 2})]


def test_parses_multiple_roles_preserving_order():
    result = parse_composition("backend:2, frontend:3, qa:1")
    assert result == [
        ("backend", {"emoji": None, "plazas": 2}),
        ("frontend", {"emoji": None, "plazas": 3}),
        ("qa", {"emoji": None, "plazas": 1}),
    ]


def test_tolerates_irregular_whitespace():
    result = parse_composition("  backend : 2 ,frontend:3 ,   qa:1   ")
    assert result == [
        ("backend", {"emoji": None, "plazas": 2}),
        ("frontend", {"emoji": None, "plazas": 3}),
        ("qa", {"emoji": None, "plazas": 1}),
    ]


def test_roles_have_no_emoji():
    (_, cfg), = parse_composition("tank:1")
    assert cfg["emoji"] is None


def test_keys_are_lowercased():
    result = parse_composition("Backend:2, FRONTEND:3")
    assert [key for key, _ in result] == ["backend", "frontend"]


def test_ignores_trailing_and_double_commas():
    result = parse_composition("backend:2,, frontend:3,")
    assert [key for key, _ in result] == ["backend", "frontend"]


@pytest.fixture
def manager(tmp_path):
    store = EventStore(str(tmp_path / "events.sqlite"), templates={})
    return EventManager(store, templates={})


def test_output_is_accepted_by_validate_composition(manager):
    # El contrato clave: la salida del parser alimenta directamente al manager.
    normalized = manager.validate_composition(parse_composition("backend:2, qa:1"))
    assert normalized == {
        "backend": {"emoji": None, "plazas": 2},
        "qa": {"emoji": None, "plazas": 1},
    }


# --------------------------------------------------------------------------- #
# Entradas inválidas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", "   ", ",", " , , "])
def test_empty_input_raises_empty(text):
    with pytest.raises(ParserCompositionError) as exc:
        parse_composition(text)
    assert exc.value.code == "empty"


def test_none_input_raises_empty():
    with pytest.raises(ParserCompositionError) as exc:
        parse_composition(None)
    assert exc.value.code == "empty"


@pytest.mark.parametrize("text", ["backend", "backend:2, frontend"])
def test_missing_colon_raises_malformed(text):
    with pytest.raises(ParserCompositionError) as exc:
        parse_composition(text)
    assert exc.value.code == "malformed"


def test_blank_role_key_raises_malformed():
    with pytest.raises(ParserCompositionError) as exc:
        parse_composition(":2")
    assert exc.value.code == "malformed"


@pytest.mark.parametrize("text", ["backend:abc", "backend:2.5", "backend:"])
def test_non_integer_plazas_raises_invalid_plazas(text):
    with pytest.raises(ParserCompositionError) as exc:
        parse_composition(text)
    assert exc.value.code == "invalid_plazas"


@pytest.mark.parametrize("text", ["backend:0", "backend:-1"])
def test_zero_or_negative_plazas_raises_invalid_plazas(text):
    with pytest.raises(ParserCompositionError) as exc:
        parse_composition(text)
    assert exc.value.code == "invalid_plazas"
    assert exc.value.role == "backend"


def test_duplicate_keys_detected_via_validate_composition(manager):
    # El parser preserva los duplicados en la lista de pares; la detección la
    # hace el manager (Req 2.5).
    pairs = parse_composition("backend:2, backend:3")
    assert [key for key, _ in pairs] == ["backend", "backend"]

    with pytest.raises(Exception) as exc:
        manager.validate_composition(pairs)
    assert exc.value.code == "duplicate_key"
    assert exc.value.role == "backend"

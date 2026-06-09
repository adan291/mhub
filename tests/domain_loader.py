"""Carga la capa de dominio (`store` y `manager`) sin pasar por el paquete
`modules.signups`, cuyo `__init__.py` importa `discord`.

La capa de dominio (EventStore, EventManager, CompositionError) no depende de
`discord`, por lo que debe poder testearse sin tener instalado ese paquete.
Estos módulos no usan imports relativos, así que se pueden cargar directamente
desde su ruta de archivo con importlib.
"""
import importlib.util
import os

_SIGNUPS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules", "signups",
)

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
)


def _load(module_name, filename):
    path = os.path.join(_SIGNUPS_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_core(module_name, filename):
    path = os.path.join(_CORE_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store_mod = _load("signups_store_under_test", "store.py")
_manager_mod = _load("signups_manager_under_test", "manager.py")
_event_bus_mod = _load_core("core_event_bus_under_test", "event_bus.py")
_guild_config_mod = _load_core(
    "core_guild_config_store_under_test", "guild_config_store.py")

EventStore = _store_mod.EventStore
EventManager = _manager_mod.EventManager
CompositionError = _manager_mod.CompositionError
EventBus = _event_bus_mod.EventBus
GuildConfigStore = _guild_config_mod.GuildConfigStore


# composition_parser depende solo de manager (que a su vez no importa discord),
# por lo que puede cargarse por ruta igual que el resto de la capa de dominio.
_composition_parser_mod = _load(
    "signups_composition_parser_under_test", "composition_parser.py")

parse_composition = _composition_parser_mod.parse_composition

# Al cargarse de forma aislada, composition_parser resuelve su propia clase
# CompositionError (cargando manager por ruta). La exponemos para que los tests
# del parser puedan capturarla con precisión.
ParserCompositionError = _composition_parser_mod.CompositionError


# --------------------------------------------------------------------------- #
# Modulo_Puente (BridgeService) y RegistryStore.
#
# Ambos módulos son agnósticos de discord, pero viven en paquetes cuyos
# `__init__.py` importan el cog (que sí depende de discord). Por eso se cargan
# directamente por ruta de archivo, igual que la capa de dominio de signups.
# Ninguno usa imports relativos, así que pueden cargarse de forma aislada.
# --------------------------------------------------------------------------- #
_BRIDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules", "bridge",
)

_REGISTRY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules", "registry",
)


def _load_from(directory, module_name, filename):
    path = os.path.join(directory, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_bridge_service_mod = _load_from(
    _BRIDGE_DIR, "bridge_service_under_test", "service.py")
_registry_store_mod = _load_from(
    _REGISTRY_DIR, "registry_store_under_test", "store.py")

BridgeService = _bridge_service_mod.BridgeService
Persona = _bridge_service_mod.Persona
RegistryStore = _registry_store_mod.RegistryStore

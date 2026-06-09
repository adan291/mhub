"""Tests del esqueleto de la API_Web (módulo `modules/api`).

`build_app` debe poder construirse y testearse SIN un bot real ni el paquete
`discord` instalado. Por eso aquí cargamos `modules/api/app.py` directamente
por ruta de archivo con importlib, evitando el `__init__.py` del paquete
`modules` (que importa `signups`/`registry` y, por tanto, `discord`).

Cubre:
  - `GET /api/health` devuelve 200 (Req 5.1, 5.3, 5.4).
  - `GET /` devuelve 200 como salud equivalente para Fly.io (Req 13.3, 13.4).
  - Las dependencias del bot (managers/stores/loop/event_bus) quedan inyectadas
    en `app.state` para las tareas posteriores (Req 5.2).
"""
import importlib.util
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient


def _load_app_module():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "modules", "api", "app.py",
    )
    spec = importlib.util.spec_from_file_location("api_app_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_app_mod = _load_app_module()
build_app = _app_mod.build_app


def _fake_bot():
    """Objeto ligero con los atributos que `build_app` espera (sin Discord)."""
    return SimpleNamespace(
        signup_manager=object(),
        registry_store=object(),
        guild_config_store=object(),
        loop=object(),
        event_bus=object(),
    )


def test_api_health_returns_200():
    client = TestClient(build_app(_fake_bot()))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_health_returns_200():
    client = TestClient(build_app(_fake_bot()))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_build_app_injects_bot_dependencies_into_state():
    bot = _fake_bot()
    app = build_app(bot)
    assert app.state.signup_manager is bot.signup_manager
    assert app.state.registry_store is bot.registry_store
    assert app.state.guild_config_store is bot.guild_config_store
    assert app.state.loop is bot.loop
    assert app.state.event_bus is bot.event_bus


def test_build_app_tolerates_missing_attributes():
    """Sin un bot completo, las dependencias quedan en None (no peta)."""
    app = build_app(SimpleNamespace())
    assert app.state.signup_manager is None
    assert app.state.registry_store is None
    assert app.state.loop is None

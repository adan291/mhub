"""Tests de la seguridad transversal de la API_Web (Req 12, task 12).

Como en `test_api_app.py`/`test_api_write.py`, `build_app` (y `auth`) se cargan
por ruta de archivo con importlib para evitar el `__init__.py` del paquete
`modules` (que importa `discord`). La app se construye con un bot ligero
(`SimpleNamespace`).

Cubre:
  - Rate limiting: con un límite `N`, la petición `N+1` dentro de la ventana
    recibe `429` (Req 12.4).
  - Rate limiting con límite `0`: TODA petición recibe `429`, incluida la de
    salud (Req 12.5).
  - Sin límite configurado: no se limita (control negativo).
  - El límite se aplica por cliente: clientes distintos no se afectan entre sí.
  - Escritura sin sesión → 401 (Req 12.2).
  - CORS restringido a `FRONTEND_ORIGIN`: se permite el origen configurado y se
    rechaza otro distinto (Req 12).
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

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


_app_mod = _load_module("app.py", "api_app_security_under_test")
_auth_mod = _load_module("auth.py", "api_auth_security_under_test")
_security_mod = _load_module("security.py", "api_security_under_test")
build_app = _app_mod.build_app
Identity = _auth_mod.Identity
SESSION_COOKIE = _auth_mod.SESSION_COOKIE
RateLimiter = _security_mod.RateLimiter


def _fake_bot():
    return SimpleNamespace(
        signup_manager=None,
        registry_store=None,
        guild_config_store=None,
        loop=object(),
        event_bus=None,
    )


# --------------------------------------------------------------------------- #
# Rate limiting (Req 12.4 / 12.5)
# --------------------------------------------------------------------------- #
def test_rate_limit_rejects_requests_over_the_limit():
    """Con límite N, la petición N+1 dentro de la ventana recibe 429 (Req 12.4)."""
    app = build_app(_fake_bot())
    app.state.rate_limit_per_minute = 3
    client = TestClient(app)

    # Las primeras 3 peticiones pasan...
    for _ in range(3):
        assert client.get("/api/health").status_code == 200
    # ...la 4ª se rechaza con 429.
    resp = client.get("/api/health")
    assert resp.status_code == 429


def test_rate_limit_zero_rejects_every_request():
    """Con límite 0, TODA petición recibe 429, incluida la de salud (Req 12.5)."""
    app = build_app(_fake_bot())
    app.state.rate_limit_per_minute = 0
    client = TestClient(app)

    assert client.get("/api/health").status_code == 429
    assert client.get("/").status_code == 429
    assert client.get("/api/events").status_code == 429


def test_no_limit_configured_does_not_throttle():
    """Sin límite configurado (None), no se limita ninguna petición."""
    app = build_app(_fake_bot())
    app.state.rate_limit_per_minute = None
    client = TestClient(app)

    for _ in range(20):
        assert client.get("/api/health").status_code == 200


def test_rate_limit_is_per_client():
    """El límite se cuenta por cliente: identidades distintas no se afectan."""
    app = build_app(_fake_bot())
    app.state.rate_limit_per_minute = 1
    sm = app.state.session_manager

    client_a = TestClient(app)
    client_a.cookies.set(SESSION_COOKIE, sm.serialize(Identity(user_id="A")))
    client_b = TestClient(app)
    client_b.cookies.set(SESSION_COOKIE, sm.serialize(Identity(user_id="B")))

    # Cada cliente agota su propio cupo de 1 petición de forma independiente.
    assert client_a.get("/api/health").status_code == 200
    assert client_b.get("/api/health").status_code == 200
    # La segunda de cada uno se rechaza.
    assert client_a.get("/api/health").status_code == 429
    assert client_b.get("/api/health").status_code == 429


def test_rate_limiter_sliding_window_unit():
    """Unidad: la ventana deslizante libera cupo al avanzar el tiempo."""
    limiter = RateLimiter(window_seconds=60.0)
    # Con límite 2: dos permitidas, la tercera no, dentro de la misma ventana.
    assert limiter.allow("k", 2, now=0.0) is True
    assert limiter.allow("k", 2, now=1.0) is True
    assert limiter.allow("k", 2, now=2.0) is False
    # Pasada la ventana (>60s desde la primera), vuelve a haber cupo.
    assert limiter.allow("k", 2, now=61.5) is True


def test_rate_limiter_zero_and_none_unit():
    """Unidad: límite 0 rechaza siempre; None nunca limita (Req 12.5)."""
    limiter = RateLimiter()
    assert limiter.allow("k", 0) is False
    assert limiter.allow("k", None) is True
    assert limiter.allow("k", None) is True


# --------------------------------------------------------------------------- #
# Escritura exige sesión (Req 12.2)
# --------------------------------------------------------------------------- #
def test_write_requires_session():
    """Una escritura sin Sesion_Web válida se rechaza con 401 (Req 12.2)."""
    app = build_app(_fake_bot())
    client = TestClient(app)
    resp = client.post("/api/events/1/join", json={"role": "backend"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# CORS restringido a FRONTEND_ORIGIN (Req 12)
# --------------------------------------------------------------------------- #
@pytest.fixture
def frontend_origin_env(monkeypatch):
    origin = "https://app.example"
    monkeypatch.setenv("FRONTEND_ORIGIN", origin)
    return origin


def test_cors_allows_configured_origin(frontend_origin_env):
    app = build_app(_fake_bot())
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Origin": frontend_origin_env})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == frontend_origin_env


def test_cors_rejects_other_origin(frontend_origin_env):
    app = build_app(_fake_bot())
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Origin": "https://evil.example"})
    # La petición se sirve (mismo proceso) pero NO se autoriza el origen ajeno:
    # no se devuelve el header de CORS para ese origen.
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"
